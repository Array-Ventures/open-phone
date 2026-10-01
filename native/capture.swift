// Original USB-only iPhone screen capture helper. No iPhone agent is installed.
import Foundation
import AVFoundation
import CoreMediaIO
import CoreImage
import ImageIO
import UniformTypeIdentifiers

let outputLock = NSLock()
func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return }
    outputLock.lock(); defer { outputLock.unlock() }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
}
enum CaptureError: Error { case message(String) }

final class Capture: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let lock = NSLock()
    let frames = DispatchQueue(label: "org.openphone.frames")
    let context = CIContext(options: [.cacheIntermediates: false])
    var session: AVCaptureSession?
    var selected: String?
    var latest: CVPixelBuffer?
    var received = Date.distantPast
    var generation = 0
    var observers: [NSObjectProtocol] = []

    func inventory() -> [[String: Any]] {
        // Muxed USB screen devices differ from Continuity Camera devices.
        AVCaptureDevice.devices().filter { $0.modelID == "iOS Device" && $0.hasMediaType(.muxed) }.map {
            ["capture_id": $0.uniqueID, "name": $0.localizedName, "connected": $0.isConnected]
        }
    }
    func status() -> [String: Any] {
        lock.lock(); let date = received; let count = generation; let hasFrame = latest != nil; lock.unlock()
        let devices = inventory()
        let connected = devices.contains { ($0["capture_id"] as? String) == selected && ($0["connected"] as? Bool) == true }
        let age = hasFrame ? max(0, Date().timeIntervalSince(date)) : nil
        return ["ok": true, "capture_id": selected as Any? ?? NSNull(),
                "running": session?.isRunning == true, "connected": connected,
                "generation": count, "frame_age_seconds": age as Any? ?? NSNull(),
                "fresh": connected && hasFrame && (age ?? .infinity) < 2]
    }
    func captureOutput(_ output: AVCaptureOutput, didOutput buffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let image = CMSampleBufferGetImageBuffer(buffer) else { return }
        lock.lock(); latest = image; received = Date(); generation += 1; lock.unlock()
    }
    func stop() {
        session?.stopRunning(); session = nil; selected = nil
        observers.forEach { NotificationCenter.default.removeObserver($0) }; observers = []
        lock.lock(); latest = nil; lock.unlock()
    }
    func start(_ id: String) throws -> [String: Any] {
        let phones = AVCaptureDevice.devices().filter { $0.modelID == "iOS Device" && $0.hasMediaType(.muxed) && $0.isConnected }
        if id == "auto", phones.count != 1 { throw CaptureError.message("Select an explicit capture_id when there are zero or multiple iPhones") }
        guard let device = phones.first(where: { id == "auto" || $0.uniqueID == id }) else {
            stop(); throw CaptureError.message("USB iPhone screen device unavailable; check cable, trust, unlock and capture ownership")
        }
        if selected == device.uniqueID, session?.isRunning == true, status()["fresh"] as? Bool == true {
            return ["ok": true, "capture_id": device.uniqueID, "name": device.localizedName]
        }
        stop()
        let s = AVCaptureSession()
        let input = try AVCaptureDeviceInput(device: device)
        let output = AVCaptureVideoDataOutput()
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        output.alwaysDiscardsLateVideoFrames = true
        output.setSampleBufferDelegate(self, queue: frames)
        s.beginConfiguration()
        guard s.canAddInput(input), s.canAddOutput(output) else { throw CaptureError.message("Cannot configure USB capture") }
        s.addInput(input); s.addOutput(output); s.commitConfiguration()
        observers.append(NotificationCenter.default.addObserver(forName: AVCaptureSession.runtimeErrorNotification, object: s, queue: nil) { note in
            emit(["event": "capture_error", "error": String(describing: note.userInfo?[AVCaptureSessionErrorKey] ?? "unknown")])
        })
        observers.append(NotificationCenter.default.addObserver(forName: AVCaptureDevice.wasDisconnectedNotification, object: device, queue: nil) { _ in
            emit(["event": "capture_disconnected", "capture_id": device.uniqueID])
        })
        session = s; selected = device.uniqueID
        s.startRunning()
        let deadline = Date().addingTimeInterval(10)
        while Date() < deadline {
            lock.lock(); let ready = latest != nil; lock.unlock()
            if ready { return ["ok": true, "capture_id": device.uniqueID, "name": device.localizedName] }
            Thread.sleep(forTimeInterval: 0.02)
        }
        stop(); throw CaptureError.message("USB capture started but delivered no frame within 10 seconds")
    }
    func screenshot(_ request: [String: Any]) throws -> [String: Any] {
        guard status()["connected"] as? Bool == true else {
            throw CaptureError.message("Selected USB screen device is unavailable; reconnect the phone before input")
        }
        let after = (request["after_generation"] as? Int) ?? -1
        let deadline = Date().addingTimeInterval(2)
        var buffer: CVPixelBuffer?; var date = Date.distantPast; var count = 0
        repeat {
            lock.lock(); buffer = latest; date = received; count = generation; lock.unlock()
            if buffer != nil && count > after && Date().timeIntervalSince(date) < 2 { break }
            Thread.sleep(forTimeInterval: 0.02)
        } while Date() < deadline
        guard let buffer, count > after, Date().timeIntervalSince(date) < 2 else { throw CaptureError.message("No fresh USB frame; the phone may be disconnected or locked") }
        var img = CIImage(cvPixelBuffer: buffer)
        let originalWidth = Int(img.extent.width), originalHeight = Int(img.extent.height)
        let maximum = (request["max_dimension"] as? Double) ?? 1600
        guard maximum >= 320, maximum <= 4096 else { throw CaptureError.message("max_dimension must be between 320 and 4096") }
        let ratio = min(1, maximum / max(img.extent.width, img.extent.height))
        img = img.transformed(by: CGAffineTransform(scaleX: ratio, y: ratio))
        let png = (request["format"] as? String) == "png"
        let type = png ? UTType.png : UTType.jpeg
        let data = NSMutableData()
        guard let image = context.createCGImage(img, from: img.extent), let dest = CGImageDestinationCreateWithData(data, type.identifier as CFString, 1, nil) else { throw CaptureError.message("Cannot encode screenshot") }
        CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: 0.8] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { throw CaptureError.message("Screenshot encoding failed") }
        return ["ok": true, "capture_id": selected as Any? ?? NSNull(), "data": data.base64EncodedString(options: []), "mime_type": png ? "image/png" : "image/jpeg", "width": image.width, "height": image.height, "screen_width": originalWidth, "screen_height": originalHeight, "generation": count, "timestamp": date.timeIntervalSince1970]
    }
    func handle(_ r: [String: Any]) throws -> [String: Any] {
        switch r["method"] as? String {
        case "list": return ["ok": true, "devices": inventory()]
        case "start": return try start((r["capture_id"] as? String) ?? "auto")
        case "screenshot": return try screenshot(r)
        case "status": return status()
        case "stop": stop(); return ["ok": true]
        default: throw CaptureError.message("Unknown capture method")
        }
    }
}

var address = CMIOObjectPropertyAddress(mSelector: UInt32(kCMIOHardwarePropertyAllowScreenCaptureDevices), mScope: UInt32(kCMIOObjectPropertyScopeGlobal), mElement: UInt32(kCMIOObjectPropertyElementMain))
var enabled: UInt32 = 1
let optIn = CMIOObjectSetPropertyData(CMIOObjectID(kCMIOObjectSystemObject), &address, 0, nil, 4, &enabled)
guard optIn == 0 else { emit(["event": "fatal", "error": "CoreMediaIO USB screen opt-in failed", "status": optIn]); exit(2) }
RunLoop.current.run(until: Date().addingTimeInterval(2))
let capture = Capture()
DispatchQueue.global(qos: .userInitiated).async {
    while let line = readLine() {
        autoreleasepool {
            var response: [String: Any]
            var id: Any = NSNull()
            do {
                guard let data = line.data(using: .utf8), let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw CaptureError.message("Invalid JSON object") }
                id = request["id"] ?? NSNull(); response = try capture.handle(request)
            } catch CaptureError.message(let message) { response = ["ok": false, "error": message] }
            catch { response = ["ok": false, "error": String(describing: error)] }
            response["id"] = id; emit(response)
        }
    }
    capture.stop(); exit(0)
}
emit(["event": "ready", "backend": "usb-avfoundation", "devices": capture.inventory()])
RunLoop.current.run()
