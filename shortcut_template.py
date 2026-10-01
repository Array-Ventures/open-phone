#!/usr/bin/env python3
"""Generate an original optional Shortcut, with no embedded runtime credentials.

Shortcuts' plist action format is undocumented; import/run validation is required
on the target phone. This generator contains no vendor workflow or vendor UUIDs.
"""
import argparse
import pathlib
import plistlib
import uuid
from urllib.parse import urlsplit


def attachment(value):
    return {'Value':value,'WFSerializationType':'WFTextTokenAttachment'}


def variable(name):
    return attachment({'Type':'Variable','VariableName':name})


def output(identifier,name='Dictionary Value'):
    return attachment({'Type':'ActionOutput','OutputUUID':identifier,'OutputName':name})


def text(value):
    return {'Value':{'string':value},'WFSerializationType':'WFTextTokenString'}


def token_text(token):
    return {'Value':{'string':'\ufffc','attachmentsByRange':{'{0, 1}':token['Value']}},'WFSerializationType':'WFTextTokenString'}


def as_text(token):
    value=dict(token['Value'])
    value['Aggrandizements']=[{'Type':'WFCoercionVariableAggrandizement',
                             'CoercionItemClass':'WFStringContentItem'}]
    return attachment(value)


def dictionary(items):
    values=[]
    for key,value in items.items():
        nested=isinstance(value,dict) and value.get('WFSerializationType')=='WFDictionaryFieldValue'
        field_value={'Value':value,'WFSerializationType':'WFDictionaryFieldValue'} if nested else value
        values.append({'WFKey':text(key),'WFItemType':1 if nested else 0,'WFValue':field_value})
    return {'Value':{'WFDictionaryFieldValueItems':values},'WFSerializationType':'WFDictionaryFieldValue'}


def workflow(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Supply an HTTP(S) origin without a path or credentials')
    actions=[]
    def action(name,**params):
        identifier=str(uuid.uuid4()).upper()
        params['UUID']=identifier
        actions.append({'WFWorkflowActionIdentifier':'is.workflow.actions.'+name,'WFWorkflowActionParameters':params})
        return identifier
    def key(source,name):return output(action('getvalueforkey',WFInput=source,WFDictionaryKey=name))
    def start_if(source,match=None,missing=False):
        grouping=str(uuid.uuid4()).upper()
        params={'WFInput':{'Type':'Variable','Variable':as_text(source) if match is not None else source},'WFControlFlowMode':0,'GroupingIdentifier':grouping,'WFCondition':101 if missing else 4}
        if match is not None:params['WFConditionalActionString']=match
        action('conditional',**params)
        return grouping
    def end_if(group):action('conditional',WFControlFlowMode=2,GroupingIdentifier=group)
    base=output(action('gettext',WFTextActionText=url.rstrip('/')),'Text')
    phone_key=output(action('gettext',WFTextActionText='REPLACE_WITH_PHONE_BRIDGE_TOKEN'),'Text')
    headers=dictionary({'X-OpenPhone-Token':token_text(phone_key)})
    def endpoint(path):
        return {'Value':{'string':'\ufffc'+path,'attachmentsByRange':{'{0, 1}':base['Value']}},'WFSerializationType':'WFTextTokenString'}
    response=output(action('downloadurl',WFURL=endpoint('/v1/bridge/claim'),WFHTTPMethod='POST',WFHTTPHeaders=headers,ShowHeaders=True,WFJSONValues=dictionary({})),'Contents of URL')
    command=key(response,'action')
    missing=start_if(command,missing=True)
    action('exit')
    end_if(missing)
    identifier=key(command,'id');receipt=key(command,'receipt');kind=key(command,'kind');payload=key(command,'payload')
    empty=output(action('gettext',WFTextActionText=''),'Text')
    action('setvariable',WFInput=empty,WFVariableName='bridge_result')
    group=start_if(kind,'copy_text')
    action('setclipboard',WFInput=key(payload,'text'),WFLocalOnly=True)
    end_if(group)
    group=start_if(kind,'read_clipboard')
    clip=output(action('getclipboard'),'Clipboard')
    action('setvariable',WFInput=clip,WFVariableName='bridge_result')
    end_if(group)
    group=start_if(kind,'open_app')
    action('openapp',WFSelectedApp=key(payload,'bundle_id'))
    end_if(group)
    group=start_if(kind,'open_url')
    action('openurl',WFInput=key(payload,'url'))
    end_if(group)
    action('downloadurl',WFURL=endpoint('/v1/bridge/complete'),WFHTTPMethod='POST',WFHTTPHeaders=headers,ShowHeaders=True,
           WFJSONValues=dictionary({'id':token_text(identifier),'receipt':token_text(receipt),
                                    'result':dictionary({'text':token_text(variable('bridge_result'))})}))
    action('exit')
    return {'WFWorkflowActions':actions,'WFWorkflowMinimumClientVersion':900,
            'WFWorkflowMinimumClientVersionString':'900','WFWorkflowHasOutputFallback':False,
            'WFWorkflowOutputContentItemClasses':[],'WFWorkflowInputContentItemClasses':[],
            'WFQuickActionSurfaces':[],'WFWorkflowTypes':['NCWidget'],
            'WFWorkflowHasShortcutInputVariables':False,
            'WFWorkflowImportQuestions':[
                {'Category':'Parameter','ActionIndex':0,'ParameterKey':'WFTextActionText',
                 'Text':'Enter the OpenPhone bridge origin reachable from this iPhone.','DefaultValue':url.rstrip('/')},
                {'Category':'Parameter','ActionIndex':1,'ParameterKey':'WFTextActionText',
                 'Text':'Enter the phone bridge key from the Mac’s owner-only .bridge-token file.',
                 'DefaultValue':'REPLACE_WITH_PHONE_BRIDGE_TOKEN'}]}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--out',type=pathlib.Path,default=pathlib.Path(__file__).parent/'private/Use OpenPhone.unsigned.shortcut')
    args=parser.parse_args()
    data=plistlib.dumps(workflow(args.url),fmt=plistlib.FMT_BINARY,sort_keys=False)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_bytes(data)
    print(f'Unsigned original Shortcut template: {args.out}; no runtime credential is embedded')


if __name__=='__main__':main()
