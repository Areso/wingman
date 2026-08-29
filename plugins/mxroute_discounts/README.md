# MXroute discount checker

Checks the annual plans at <https://mxroute.com/#plans> against the normal prices
stored in `config.toml`. It prints one line per discounted plan and prints nothing
when all prices are normal and no promotion language is present. It also checks
visible page text for the words `sale`, `special`, `offer`, and `discount`.

The plugin exits with an error if the page cannot be fetched, its plan markup can
no longer be parsed, or a configured plan is missing. Update `[normal_prices]` in
`config.toml` when MXroute permanently changes its standard pricing.
