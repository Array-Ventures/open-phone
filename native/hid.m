// Original local Bluetooth HID transport. Uses macOS private CoreBluetooth ABI.
// Private API declarations describe the installed framework's transport ABI.
#import <Foundation/Foundation.h>
#import <CoreBluetooth/CoreBluetooth.h>
#import <objc/runtime.h>
#import <sys/socket.h>
#import <unistd.h>
#import <fcntl.h>
#import <math.h>

@interface OPClassicPeer : NSObject
@property(readonly) NSString *addressString;
@property(readonly) NSInteger state;
- (void)openL2CAPChannel:(uint16_t)psm;
- (void)closeL2CAPChannel:(uint16_t)psm;
- (void)setConnectL2CAPCallback:(void (^)(CBL2CAPChannel *, NSInteger))block;
- (void)setDisconnectL2CAPCallback:(void (^)(CBL2CAPChannel *, NSInteger))block;
- (void)handleL2CAPChannelOpened:(NSDictionary *)args;
- (void)handleL2CAPChannelClosed:(NSDictionary *)args;
- (id)channelWithPSM:(uint16_t)psm;
@end

@interface OPClassicManager : NSObject
@property(readonly) NSInteger state;
@property(readonly) NSInteger powerState;
@property(readonly) BOOL tccApproved;
@property(readonly) BOOL connectable;
@property(readonly) BOOL discoverable;
- (instancetype)initWithQueue:(dispatch_queue_t)queue options:(NSDictionary *)options;
- (void)setTccApproved:(BOOL)value;
- (void)doneWithTCC;
- (void)setBTConnectable:(BOOL)value;
- (void)setBTDiscoverable:(BOOL)value;
- (uint32_t)addServiceWithData:(NSData *)data;
- (void)removeServiceHandle:(uint32_t)handle;
- (OPClassicPeer *)retrievePeerWithAddress:(NSString *)address;
- (void)connectPeer:(OPClassicPeer *)peer options:(NSDictionary *)options;
- (void)cancelPeerConnection:(OPClassicPeer *)peer force:(BOOL)force;
- (void)setConnectCallback:(void (^)(OPClassicPeer *, NSInteger))block;
- (void)setDisconnectCallback:(void (^)(OPClassicPeer *, NSInteger))block;
- (id)getLocalSDPDatabase;
- (NSArray *)retrievePairedPeersWithOptions:(NSDictionary *)options;
@end

@interface CBL2CAPChannel (OPPrivate)
@property(readonly) OPClassicPeer *peer;
@property(readonly) int socketFD;
@property(readonly) uint16_t outgoingMTU;
- (void)sendData:(NSData *)data withCompletion:(void (^)(NSInteger))completion;
@end

static NSLock *printLock;
static void emit(NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    [printLock lock];
    fwrite(data.bytes,1,data.length,stdout); fputc('\n',stdout); fflush(stdout);
    [printLock unlock];
}
static void event(NSString *kind, NSDictionary *value) {
    NSMutableDictionary *d = [value mutableCopy]; d[@"event"] = kind; emit(d);
}

@interface Controller : NSObject<CBCentralManagerDelegate>
@property(nonatomic,strong) CBCentralManager *permissionManager;
@property(nonatomic,strong) OPClassicManager *manager;
@property(nonatomic,strong) dispatch_queue_t queue;
@property(nonatomic,strong) NSMutableDictionary<NSString *,NSMutableDictionary *> *hosts;
@property(nonatomic,strong) NSMutableDictionary<NSString *,dispatch_source_t> *readers;
@property(nonatomic,strong) NSMutableDictionary<NSString *,CBL2CAPChannel *> *channels;
@property(nonatomic,strong) NSMutableDictionary<NSString *,NSMutableDictionary<NSNumber *,NSData *> *> *lastReports;
@property(nonatomic,strong) NSMutableDictionary<NSString *,NSDate *> *pendingOpens;
@property uint32_t serviceHandle;
@property(nonatomic,strong) NSData *service;
@property(nonatomic,strong) NSLock *lock;
- (void)opened:(CBL2CAPChannel *)channel status:(NSInteger)status;
- (void)closed:(CBL2CAPChannel *)channel status:(NSInteger)status;
- (void)openFailed:(OPClassicPeer *)peer psm:(uint16_t)psm result:(int)result;
@end

static Controller *controller;
static IMP oldPeerMessage;

// Incoming Classic HID channels do not have callbacks in the public BLE API.
// Install callbacks only on HID PSMs, inside this process, before framework routing.
static void peerMessage(id peer, SEL selector, int message, NSDictionary *args) {
    uint16_t psm = [args[@"kCBMsgArgPSM"] unsignedShortValue];
    if (controller && (psm == 0x11 || psm == 0x13)) {
        event(@"hid_peer_message",@{@"message":@(message),@"args":args.description ?: @""});
        if (message == 27 && [args[@"kCBMsgArgResult"] intValue] == 0) {
            [(OPClassicPeer *)peer setConnectL2CAPCallback:^(CBL2CAPChannel *ch,NSInteger status){ [controller opened:ch status:status]; }];
            [(OPClassicPeer *)peer setDisconnectL2CAPCallback:^(CBL2CAPChannel *ch,NSInteger status){ [controller closed:ch status:status]; }];
            [(OPClassicPeer *)peer handleL2CAPChannelOpened:args];
            return;
        }
        if(message==27)[controller openFailed:(OPClassicPeer *)peer psm:psm result:[args[@"kCBMsgArgResult"] intValue]];
        if (message == 28) {
            [(OPClassicPeer *)peer setDisconnectL2CAPCallback:^(CBL2CAPChannel *ch,NSInteger status){ [controller closed:ch status:status]; }];
            [(OPClassicPeer *)peer handleL2CAPChannelClosed:args];
            return;
        }
    }
    ((void(*)(id,SEL,int,id))oldPeerMessage)(peer,selector,message,args);
}

static ssize_t packetWrite(int fd, NSData *packet) {
    // CoreBluetooth uses a local stream socket; an empty rights control message
    // uses this ancillary header in its Bluetooth transport. Packet-boundary
    // semantics are inferred from the working transport, not a public API.
    struct iovec io = {(void *)packet.bytes, packet.length};
    struct cmsghdr control = { .cmsg_len=sizeof(struct cmsghdr), .cmsg_level=SOL_SOCKET, .cmsg_type=SCM_RIGHTS };
    struct msghdr msg = {0};
    msg.msg_iov=&io; msg.msg_iovlen=1; msg.msg_control=&control; msg.msg_controllen=sizeof(control);
    return sendmsg(fd,&msg,MSG_DONTWAIT);
}

static BOOL validReport(NSData *report) {
    if(report.length!=9)return NO;
    uint8_t identifier=((const uint8_t *)report.bytes)[0];
    return identifier==1 || identifier==2;
}

@implementation Controller
- (instancetype)init {
    if ((self=[super init])) {
        self.queue=dispatch_queue_create("org.openphone.hid",DISPATCH_QUEUE_SERIAL);
        self.hosts=[NSMutableDictionary dictionary]; self.readers=[NSMutableDictionary dictionary]; self.channels=[NSMutableDictionary dictionary]; self.lastReports=[NSMutableDictionary dictionary]; self.pendingOpens=[NSMutableDictionary dictionary]; self.lock=[NSLock new];
    }
    return self;
}
- (void)centralManagerDidUpdateState:(CBCentralManager *)central {
    event(@"bluetooth_permission",@{@"state":@(central.state),@"authorization":@(CBManager.authorization)});
}
- (void)opened:(CBL2CAPChannel *)ch status:(NSInteger)status {
    NSString *address=((OPClassicPeer *)ch.peer).addressString ?: @"unknown";
    event(@"channel_open",@{@"address":address,@"psm":@(ch.PSM),@"status":@(status),@"fd":@(ch.socketFD),@"mtu":@(ch.outgoingMTU)});
    if (status != 0 || ch.socketFD < 0) return;
    NSString *key=[NSString stringWithFormat:@"%@/%u",address,ch.PSM];
    [self.lock lock];
    [self.pendingOpens removeObjectForKey:key];
    NSMutableDictionary *host=self.hosts[address];
    if (!host) { host=[NSMutableDictionary dictionary]; self.hosts[address]=host; }
    host[ch.PSM==0x11 ? @"control_fd" : @"interrupt_fd"]=@(ch.socketFD);
    self.channels[key]=ch;
    dispatch_source_t prior=self.readers[key]; if (prior) dispatch_source_cancel(prior);
    int fd=ch.socketFD; fcntl(fd,F_SETFL,fcntl(fd,F_GETFL)|O_NONBLOCK);
    int noSignal=1; setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&noSignal,sizeof(noSignal));
    dispatch_source_t source=dispatch_source_create(DISPATCH_SOURCE_TYPE_READ,(uintptr_t)fd,0,self.queue);
    self.readers[key]=source;
    [self.lock unlock];
    dispatch_source_set_event_handler(source,^{
        uint8_t buffer[4096]; ssize_t n=recv(fd,buffer,sizeof(buffer),MSG_DONTWAIT);
        if (n<=0) { if (n==0 || (errno!=EAGAIN && errno!=EINTR)) [self closed:ch status:0]; return; }
        NSData *packet=[NSData dataWithBytes:buffer length:(NSUInteger)n];
        event(@"hid_rx",@{@"address":address,@"psm":@(ch.PSM),@"data":[packet base64EncodedStringWithOptions:0]});
        if (ch.PSM != 0x11) return;
        uint8_t reply[16]={0}; size_t count=1; uint8_t kind=buffer[0]&0xf0;
        if (kind==0x70) reply[0]=(buffer[0]&1) ? 0 : 4;
        else if (kind==0x60) { reply[0]=0xa0; reply[1]=1; count=2; }
        else if (kind==0x40) {
            uint8_t reportID=n>1 ? buffer[1] : 1;
            reply[0]=0xa1; reply[1]=reportID;
            if(reportID!=1 && reportID!=2){reply[0]=2;count=1;}
            else {
                count=10;
                [self.lock lock]; NSData *last=self.lastReports[address][@(reportID)]; [self.lock unlock];
                if(last.length==9)memcpy(reply+1,last.bytes,9);
            }
        }
        else if (kind==0x50 || kind==0x90) reply[0]=0;
        else if (kind==0x80) { reply[0]=0xa0; reply[1]=0; count=2; }
        else if (kind==0x10) { event(@"hid_control",@{@"command":@(buffer[0]&15)}); return; }
        else reply[0]=3;
        NSData *response=[NSData dataWithBytes:reply length:count];
        ssize_t wrote=packetWrite(fd,response);
        event(@"hid_control_reply",@{@"bytes":@(wrote),@"errno":@(wrote<0 ? errno : 0)});
    });
    dispatch_resume(source);
}
- (void)closed:(CBL2CAPChannel *)ch status:(NSInteger)status {
    NSString *address=((OPClassicPeer *)ch.peer).addressString ?: @"unknown";
    NSString *key=[NSString stringWithFormat:@"%@/%u",address,ch.PSM];
    [self.lock lock];
    // A late close for a replaced channel must not erase the new connection.
    if(self.channels[key] && self.channels[key]!=ch){[self.lock unlock];return;}
    dispatch_source_t source=self.readers[key]; if(source)dispatch_source_cancel(source);
    [self.readers removeObjectForKey:key]; [self.channels removeObjectForKey:key];
    [self.hosts[address] removeObjectForKey:ch.PSM==0x11 ? @"control_fd" : @"interrupt_fd"];
    [self.lock unlock];
    event(@"channel_close",@{@"address":address,@"psm":@(ch.PSM),@"status":@(status)});
}
- (void)openFailed:(OPClassicPeer *)peer psm:(uint16_t)psm result:(int)result {
    NSString *address=peer.addressString ?: @"unknown";
    NSString *key=[NSString stringWithFormat:@"%@/%u",address,psm];
    [self.lock lock];[self.pendingOpens removeObjectForKey:key];[self.lock unlock];
    event(@"channel_open_failed",@{@"address":address,@"psm":@(psm),@"result":@(result)});
}
- (NSDictionary *)handle:(NSDictionary *)request {
    NSString *method=request[@"method"];
    if ([method isEqual:@"initialize"]) {
        // An instance created before the first permission grant can remain
        // unsupported. Recreate only after the normal OS grant is visible.
        if(self.manager && self.manager.state==2 && CBManager.authorization==CBManagerAuthorizationAllowedAlways)self.manager=nil;
        if(self.manager)return @{@"ok":@YES,@"state":@(self.manager.state)};
        if(!self.permissionManager)self.permissionManager=[[CBCentralManager alloc] initWithDelegate:self queue:self.queue options:@{CBCentralManagerOptionShowPowerAlertKey:@NO}];
        Class cls=NSClassFromString(@"CBClassicManager");
        if(!cls)return @{@"ok":@NO,@"error":@"CBClassicManager unavailable"};
        self.manager=[(OPClassicManager *)[cls alloc] initWithQueue:self.queue options:@{@"kCBMsgArgIsPrivilegedDaemon":@YES}];
        [self.manager setConnectCallback:^(OPClassicPeer *peer,NSInteger status){
            event(@"peer_connected",@{@"address":peer.addressString ?: @"",@"status":@(status)});
            // The caller opens channels after observing the connection state.
            // Opening here too raced Python's poller and duplicated both requests.
        }];
        [self.manager setDisconnectCallback:^(OPClassicPeer *peer,NSInteger status){ event(@"peer_disconnected",@{@"address":peer.addressString ?: @"",@"status":@(status)}); }];
        return @{@"ok":@(self.manager!=nil),@"state":@(self.manager.state),@"authorization":@(CBManager.authorization)};
    }
    if([method isEqual:@"status"]){
        [self.lock lock]; NSDictionary *hosts=[[NSDictionary alloc] initWithDictionary:self.hosts copyItems:YES]; [self.lock unlock];
        return @{@"ok":@YES,@"initialized":@(self.manager!=nil),@"state":@(self.manager ? self.manager.state : -1),@"power_state":@(self.manager ? self.manager.powerState : -1),@"tcc_approved":@(self.manager && self.manager.tccApproved),@"authorization":@(CBManager.authorization),@"service_handle":@(self.serviceHandle),@"hosts":hosts};
    }
    if(!self.manager)return @{@"ok":@NO,@"error":@"Initialize first"};
    if([method isEqual:@"database"]){
        id db=[self.manager getLocalSDPDatabase];
        if([db isKindOfClass:[NSData class]])return @{@"ok":@YES,@"class":NSStringFromClass([db class]),@"data":[db base64EncodedStringWithOptions:0]};
        return @{@"ok":@YES,@"class":db ? NSStringFromClass([db class]) : @"nil",@"description":[db description] ?: @""};
    }
    if([method isEqual:@"advertise"]){
        if(CBManager.authorization != CBManagerAuthorizationAllowedAlways)return @{@"ok":@NO,@"error":@"Bluetooth permission must be granted first",@"authorization":@(CBManager.authorization)};
        [self.manager setTccApproved:YES];
        [self.manager doneWithTCC];
        self.service=[[NSData alloc] initWithBase64EncodedString:request[@"sdp"] options:0];
        if(!self.service.length)return @{@"ok":@NO,@"error":@"Missing SDP record"};
        if(self.serviceHandle)[self.manager removeServiceHandle:self.serviceHandle];
        self.serviceHandle=[self.manager addServiceWithData:self.service];
        [self.manager setBTConnectable:YES]; [self.manager setBTDiscoverable:YES];
        return @{@"ok":@(self.serviceHandle!=0),@"service_handle":@(self.serviceHandle),@"connectable":@(self.manager.connectable),@"discoverable":@(self.manager.discoverable)};
    }
    if([method isEqual:@"connect"]){
        NSString *address=request[@"address"];
        OPClassicPeer *peer=[self.manager retrievePeerWithAddress:address];
        if(!peer)return @{@"ok":@NO,@"error":@"No known paired Classic peer",@"address":address};
        [peer setConnectL2CAPCallback:^(CBL2CAPChannel *ch,NSInteger status){ [self opened:ch status:status]; }];
        [peer setDisconnectL2CAPCallback:^(CBL2CAPChannel *ch,NSInteger status){ [self closed:ch status:status]; }];
        [self.manager connectPeer:peer options:@{}];
        return @{@"ok":@YES,@"address":address,@"peer_state":@(peer.state)};
    }
    if([method isEqual:@"peer_status"]){
        OPClassicPeer *peer=[self.manager retrievePeerWithAddress:request[@"address"]];
        return @{@"ok":@(peer!=nil),@"peer_state":@(peer ? peer.state : -1)};
    }
    if([method isEqual:@"open_channels"]){
        OPClassicPeer *peer=[self.manager retrievePeerWithAddress:request[@"address"]];
        if(!peer)return @{@"ok":@NO,@"error":@"Unknown peer"};
        [peer setConnectL2CAPCallback:^(CBL2CAPChannel *ch,NSInteger status){ [self opened:ch status:status]; }];
        [peer setDisconnectL2CAPCallback:^(CBL2CAPChannel *ch,NSInteger status){ [self closed:ch status:status]; }];
        NSMutableArray *requested=[NSMutableArray array];
        for(NSNumber *number in @[@0x11,@0x13]){
            uint16_t psm=number.unsignedShortValue;
            NSString *key=[NSString stringWithFormat:@"%@/%u",peer.addressString,psm];
            [self.lock lock];
            NSDate *pending=self.pendingOpens[key];
            BOOL skip=self.channels[key]!=nil || (pending && -pending.timeIntervalSinceNow<2);
            if(!skip)self.pendingOpens[key]=[NSDate date];
            [self.lock unlock];
            if(!skip){[requested addObject:number];[peer openL2CAPChannel:psm];}
        }
        return @{@"ok":@YES,@"peer_state":@(peer.state),@"requested_psms":requested};
    }
    if([method isEqual:@"peers"]){
        NSMutableArray *peers=[NSMutableArray array];
        for(OPClassicPeer *peer in [self.manager retrievePairedPeersWithOptions:@{}])
            [peers addObject:@{@"address":peer.addressString ?: @"",@"state":@(peer.state)}];
        return @{@"ok":@YES,@"peers":peers};
    }
    if([method isEqual:@"report"]){
        NSString *address=request[@"address"]; NSData *report=[[NSData alloc] initWithBase64EncodedString:request[@"data"] options:0];
        if(!validReport(report))return @{@"ok":@NO,@"error":@"Report does not match the HID descriptor"};
        const uint8_t *bytes=report.bytes;
        NSString *key=[NSString stringWithFormat:@"%@/19",address];
        [self.lock lock]; NSNumber *fd=self.hosts[address][@"interrupt_fd"]; CBL2CAPChannel *channel=self.channels[key]; [self.lock unlock];
        if(!fd)return @{@"ok":@NO,@"error":@"No connected HID interrupt channel"};
        if(report.length+1>channel.outgoingMTU)return @{@"ok":@NO,@"error":@"Report exceeds channel MTU"};
        uint8_t header=0xa1; NSMutableData *packet=[NSMutableData dataWithBytes:&header length:1]; [packet appendData:report];
        ssize_t n=packetWrite(fd.intValue,packet);
        if(n==(ssize_t)packet.length){
            [self.lock lock];
            if(!self.lastReports[address])self.lastReports[address]=[NSMutableDictionary dictionary];
            self.lastReports[address][@(bytes[0])]=report;
            [self.lock unlock];
        }
        return @{@"ok":@(n==(ssize_t)packet.length),@"bytes":@(n),@"errno":@(n<0 ? errno : 0)};
    }
    if([method isEqual:@"sequence"]){
        NSArray *reports=request[@"reports"];
        if(![reports isKindOfClass:[NSArray class]] || reports.count>1000)return @{@"ok":@NO,@"error":@"Invalid report sequence"};
        double duration=0;
        for(NSDictionary *r in reports){
            if(![r isKindOfClass:[NSDictionary class]] || ![r[@"data"] isKindOfClass:[NSString class]])return @{@"ok":@NO,@"error":@"Invalid report sequence entry"};
            if(!validReport([[NSData alloc] initWithBase64EncodedString:r[@"data"] options:0]))return @{@"ok":@NO,@"error":@"Sequence contains a report that does not match the descriptor"};
            double delay=[r[@"delay_ms"] doubleValue];
            if(!isfinite(delay) || delay<0 || delay>4000)return @{@"ok":@NO,@"error":@"Invalid report delay"};
            duration+=delay;
        }
        if(duration>15000)return @{@"ok":@NO,@"error":@"Sequence exceeds 15 seconds"};
        NSUInteger index=0;
        for(NSDictionary *r in reports){
            usleep((useconds_t)([r[@"delay_ms"] doubleValue]*1000));
            NSDictionary *result=[self handle:@{@"method":@"report",@"address":request[@"address"],@"data":r[@"data"] ?: @""}];
            if(![result[@"ok"] boolValue])return @{@"ok":@NO,@"index":@(index),@"error":result[@"error"] ?: @"Report write failed"};
            index++;
        }
        return @{@"ok":@YES,@"reports":@(index),@"duration_ms":@(duration)};
    }
    if([method isEqual:@"stop"]){
        // Release this helper's keys/buttons before closing a live HID service.
        // This also covers EOF during a caller's manually held gesture.
        [self.lock lock]; NSArray *addresses=self.hosts.allKeys; [self.lock unlock];
        for(NSString *address in addresses){
            [self.lock lock]; NSData *last=self.lastReports[address][@2]; [self.lock unlock];
            uint8_t pointer[9]={2};
            if(last.length==9)memcpy(pointer+5,(const uint8_t *)last.bytes+5,4);
            uint8_t keyboard[9]={1};
            for(NSData *release in @[[NSData dataWithBytes:pointer length:9],[NSData dataWithBytes:keyboard length:9]])
                [self handle:@{@"method":@"report",@"address":address,@"data":[release base64EncodedStringWithOptions:0]}];
        }
        if(addresses.count)usleep(60000);
        // Ask the daemon to close our channels before teardown, so the next
        // helper need not race implicit socket cleanup.
        for(NSString *address in addresses){
            OPClassicPeer *peer=[self.manager retrievePeerWithAddress:address];
            for(NSNumber *number in @[@0x13,@0x11]){
                NSString *key=[NSString stringWithFormat:@"%@/%u",address,number.unsignedShortValue];
                [self.lock lock];BOOL owned=self.channels[key]!=nil;[self.lock unlock];
                if(owned)[peer closeL2CAPChannel:number.unsignedShortValue];
            }
        }
        if(addresses.count)usleep(150000);
        if(self.serviceHandle){[self.manager removeServiceHandle:self.serviceHandle];self.serviceHandle=0;}
        [self.lock lock]; for(dispatch_source_t source in self.readers.allValues)dispatch_source_cancel(source); [self.readers removeAllObjects]; [self.channels removeAllObjects]; [self.hosts removeAllObjects]; [self.lastReports removeAllObjects]; [self.pendingOpens removeAllObjects]; [self.lock unlock];
        return @{@"ok":@YES};
    }
    return @{@"ok":@NO,@"error":@"Unknown method"};
}
@end

int main(void){
    @autoreleasepool{
        printLock=[NSLock new]; controller=[Controller new];
        Method peer=class_getInstanceMethod(NSClassFromString(@"CBClassicPeer"),NSSelectorFromString(@"handleMsg:args:"));
        if(!peer){emit(@{@"event":@"fatal",@"error":@"CoreBluetooth Classic ABI missing"});return 2;}
        oldPeerMessage=method_setImplementation(peer,(IMP)peerMessage);
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^{
            char *line=NULL; size_t cap=0;
            while(getline(&line,&cap,stdin)>0){
                @autoreleasepool{
                    NSData *data=[NSData dataWithBytes:line length:strlen(line)]; NSError *error=nil;
                    NSDictionary *request=[NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
                    if(![request isKindOfClass:[NSDictionary class]]){emit(@{@"ok":@NO,@"error":@"Invalid JSON request"});continue;}
                    @try{ NSMutableDictionary *response=[[controller handle:request] mutableCopy]; response[@"id"]=request[@"id"] ?: [NSNull null]; emit(response); }
                    @catch(NSException *exception){emit(@{@"ok":@NO,@"id":request[@"id"] ?: [NSNull null],@"error":exception.reason ?: exception.name});}
                }
            }
            free(line); [controller handle:@{@"method":@"stop"}]; exit(0);
        });
        emit(@{@"event":@"ready",@"backend":@"corebluetooth-classic"});
        [[NSRunLoop currentRunLoop] run];
    }
    return 0;
}
