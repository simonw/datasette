# Notes for coding agents

## JavaScript

- Never use the browser's native `alert()`, `confirm()` or `prompt()` dialogs. Browsers embedded in other applications can dismiss them without showing anything, so a button that depends on one silently does nothing. Ask with `DatasetteModal.confirm()` from `datasette/static/modal.js`, or with a step inside the dialog that is already open. Show errors in the page, next to what failed, or with `DatasetteModal.alert()` when there is no better place for them.
