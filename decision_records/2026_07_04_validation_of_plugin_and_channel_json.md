# Validation logic decisions
decision record created: 2026-07-04  
decision record revised: 2026-07-04
decision record author: Anton Areso Gladyshev  
## Abstract
This decision record holds information about `plugin.json` and `channel.json` processing by the Core (`wingman`).
## Decisions
- if id of the plugin/channel is longer 96 sym, it would throw the error
- id can not be empty
- for channels: ports should be in range 1 to 65000
- for channels: Address, Endpoint, EndpointToDef cannot be empty
- for plugins: Name, InvocationWith, InvocationFile aren't empty
- for plugins: invocation_timeout_s must be positive
- for plugins: cron_time cannot be empty if Cron is true
