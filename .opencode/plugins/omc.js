/**
 * omc plugin for OpenCode.ai — registers the omc skills directory via the
 * config hook (no symlinks needed).
 */
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const OmcPlugin = async () => {
  const omcSkillsDir = path.resolve(__dirname, '../../skills');
  return {
    config: async (config) => {
      config.skills = config.skills || {};
      config.skills.paths = config.skills.paths || [];
      if (!config.skills.paths.includes(omcSkillsDir)) {
        config.skills.paths.push(omcSkillsDir);
      }
    },
  };
};
