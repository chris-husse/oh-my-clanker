# Installing omc for OpenCode

Add omc to the `plugin` array in your `opencode.json` (global:
`~/.config/opencode/opencode.json`, or project-level):

```json
{ "plugin": ["omc@git+https://github.com/chris-husse/oh-my-clanker.git"] }
```

Restart OpenCode. Verify with: "use the skill tool to list skills" — `slug`
and `start` should appear. omc's start skill hands off to superpowers'
brainstorming skill — install superpowers for OpenCode too:
https://github.com/obra/superpowers (docs/README.opencode.md).
