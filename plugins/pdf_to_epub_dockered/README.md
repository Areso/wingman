# Dockerized PDF to EPUB plugin for Wingman

Converts digital, scanned, and mixed PDFs to validated EPUB files. Calibre, OCRmyPDF, Tesseract, Poppler, Ghostscript, and all native libraries run inside a container; the host only needs Docker.

## Supported platforms

- macOS on Apple Silicon: builds and runs `linux/arm64` through Docker Desktop.
- Linux x86-64: builds and runs `linux/amd64`.
- Linux ARM64: supported by the same Dockerfile and `linux/arm64` image.
- Multi-platform publishing: Docker Buildx can publish one image containing both architectures.

The Dockerfile has no architecture pin. Its Python and Debian base images and installed Debian packages are available for both `amd64` and `arm64`.

## Install

Install Docker Desktop on macOS or Docker Engine with Buildx on Linux. Then, from the Wingman repository root:

```bash
unzip wingman-pdf-to-epub.zip -d plugins/
cd plugins/wingman-pdf-to-epub
chmod +x run.sh build_image.sh install_dependencies.sh
./install_dependencies.sh
```

The install script builds `wingman-pdf-to-epub:local` for the host's native architecture. Restart Wingman so it reloads `plugin.json`.

## Usage

Pass a local PDF path as Wingman user input:

```text
/data/books/example.pdf
```

An optional second positional path controls the output:

```text
/data/books/example.pdf /data/exports/example.epub
```

Direct smoke tests:

```bash
./run.sh /data/books/text.pdf
./run.sh /data/books/scan.pdf /data/books/scan.epub --language eng
./run.sh /data/books/mixed.pdf --language eng+deu --title "My Book" --author "A. Writer"
```

Supported switches include `--language`, `--title`, `--author`, `--force-ocr`, and `--no-ocr`. Run `docker run --rm wingman-pdf-to-epub:local --help` for the complete list.

Only the input and output directories are mounted. When they differ, the input mount is read-only. Runtime networking is disabled, and the container runs with the Wingman process's numeric user and group IDs so Linux output files are not owned by root.

## Multi-platform images

Normal installation builds only the current machine's architecture. To publish a reusable image for macOS ARM, Linux x86-64, and Linux ARM64, log in to a container registry and run:

```bash
./build_image.sh \
  --platform linux/amd64,linux/arm64 \
  --tag ghcr.io/YOUR_ACCOUNT/wingman-pdf-to-epub:latest \
  --push
```

Then configure Wingman to use it:

```bash
export WINGMAN_PDF_TO_EPUB_IMAGE=ghcr.io/YOUR_ACCOUNT/wingman-pdf-to-epub:latest
```

Docker automatically pulls the matching architecture from the image manifest. Cross-building may require a container-backed Buildx builder with QEMU; native builds on each target do not.

## Storage and removal

The conversion dependencies remain inside the Docker image rather than Homebrew or the Linux host. To remove them:

```bash
docker image rm wingman-pdf-to-epub:local
```

Conversion quality depends on the source layout. Complex multi-column documents may still need Calibre-specific tuning.

```
brew install docker-buildx
```
or
```
sudo apt install docker-buildx-plugin
```