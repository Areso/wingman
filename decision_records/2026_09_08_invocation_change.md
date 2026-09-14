# Invocation change decision
decision record created: 2026-09-08  
decision record author: Anton Areso Gladyshev  
## Abstract
This decision record holds information about `plugin.json` processing by the Core (`wingman`).
## Decisions
Moved from 
```
"invocation_with": "bash",
"invocation_file": "run.sh",
```

```
"entrypoint": {
"executable": ".venv/bin/python".
'args": ["poetry.py"]
}
```
which helpful to have multiple parameters as an array (list) of the args.  
