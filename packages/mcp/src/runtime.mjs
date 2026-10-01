import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join, resolve} from 'node:path';

export const projectRoot=fileURLToPath(new URL('../../../',import.meta.url));
export function pythonExecutable(env=process.env){
  if(env.OPEN_PHONE_PYTHON)return env.OPEN_PHONE_PYTHON;
  const local=join(projectRoot,'.venv','bin','python');
  return existsSync(local)?local:'python3';
}
export function runtimePath(name,env=process.env){
  return join(resolve(env.OPEN_PHONE_HOME||projectRoot),name);
}
