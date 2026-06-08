# tailscale-domain

`tsd` is a small local helper for mapping memorable Tailscale hostnames to local ports.

It stores config in:
- `~/.config/tsd/routes.json`
- `~/.config/tsd/routes.caddy`

## Commands

- `tsd init` — initialize config and remember the base device domain
- `tsd add` — add a route interactively
- `tsd add is 9010 --workdir ~/Code/instagram-slides --path /instagram-slides` — add/update a route directly
- `tsd list` — show current routes
- `tsd rm` / `tsd remove` — show routes and remove one
- `tsd apply` — regenerate the Caddy snippet

## Install locally on this Mac

```bash
cd ~/Code/tailscale-domain
python3 -m pip install -e .
```

That exposes the `tsd` command in your environment.

## Example

```bash
tsd init
tsd add is 9010 --workdir ~/Code/instagram-slides --path /instagram-slides
tsd list
tsd apply
```

## Notes

- `domain` is derived from the key and the configured device domain.
- `path` is optional and only used when you want path-based proxying.
- No secrets are stored; the config only keeps route metadata.
