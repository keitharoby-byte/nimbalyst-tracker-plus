import { activateBackend } from './backend';
import type { BackendContext } from './contracts';

export async function activate(context: BackendContext) {
  return await activateBackend(context, 'read');
}

export default { activate };
