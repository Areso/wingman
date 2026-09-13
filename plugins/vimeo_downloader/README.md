# Wingman Vimeo downloader plugin

This plugin follows the native plugin format used by `github.com/Areso/wingman`.
It accepts user text through Wingman's `user_input` flow and saves one media item locally.

## Install

Copy this directory into `plugins/vimeo_downloader` in your Wingman checkout, then run:

```sh
cd plugins/vimeo_downloader
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Restart Wingman and its Telegram channel so the plugin manifest is reloaded.

## Use

When the plugin prompts for input, send one of:

```text
https://vimeo.com/...
video https://vimeo.com/...
audio https://vimeo.com/...
```

A bare URL defaults to `video`.

Downloaded files go to `plugins/vimeo_downloader/downloads/` by default. To keep downloads
outside the source tree, set `WINGMAN_MEDIA_DIR` for the Wingman process; this plugin
then writes to `$WINGMAN_MEDIA_DIR/vimeo/`.

`video` prefers a single-file MP4 so ffmpeg is not required for merging.
`audio` prefers M4A and otherwise keeps the best available audio container.

The plugin intentionally disables playlists and limits each invocation to one item.
It also rejects non-Vimeo hosts and URLs containing embedded credentials.

Only use this with media you own or have permission/legal authorization to download.
Platform terms and copyright rules may restrict downloading.

## Tests

The included tests validate argument parsing and source-domain restrictions without
making network requests:

```sh
python3 -m unittest -v test_downloader.py
```
