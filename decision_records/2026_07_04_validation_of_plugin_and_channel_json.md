# Validation logic decisions
decision record created: 2026-07-04  
decision record revised: 2026-09-09
decision record author: Anton Areso Gladyshev  
## Abstract
This decision record holds information about `plugin.json` and `channel.json` processing by the Core (`wingman`).
## Decisions
- if id of the plugin/channel is longer 96 sym, it would throw the error
- id can not be empty
- for channels: ports should be in range 1 to 65535
- for channels: Address, Endpoint, EndpointToDef cannot be empty
- for plugins: Name and entrypoint.executable aren't empty, and entrypoint.args is present
- for plugins: invocation_timeout_s must be positive
- for plugins: cron_time cannot be empty if Cron is true
