# Validation logic decisions
decision record created: 2026-07-04  
decision record revised:  
decision record author: Anton Areso Gladyshev  
## Abstract
This decision record holds information about `plugin.json` and `channel.json` processing by the Core (`wingman`).
## Decisions and known tradeoffs
Current validation logic requires all fields on the `Plugin` and `Channel` structs to be presented in the files (with exception for `Dir` string property).  
Also, for integer fields (like `invocation_timeout_s`), 0 aren't accepted (because it if is not filled, by Unmarshalling it gets false, which translates to 0).   
If any error occurs, it would show user not a json field, but the struct property.  
For now, it doesn't validate disabled plugins and jsons.  
