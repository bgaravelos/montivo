# Montivo in a container: the editor (`montivo serve`) and the CLI with ffmpeg, Python 3.12 and the pinned packages,
# the same stack as the hosted demo's Modal image. Run folders and footage live in /data, a volume.
#
#   docker compose up --build                       # the editor on http://localhost:8765, keys from .env if present
#   docker compose run --rm montivo sample           # the 015 sample run into ./data (no key needed to open it)
#   docker compose run --rm montivo montivo run footage/clip.mp4 --brief "..." --out runs/001
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml constraints.txt README.md LICENSE ./
COPY montivo ./montivo
RUN pip install --no-cache-dir . -c constraints.txt \
    && printf '#!/bin/sh\nexec python -m montivo.server.sample fetch --root /data "$@"\n' > /usr/local/bin/sample \
    && chmod +x /usr/local/bin/sample

WORKDIR /data
VOLUME /data
EXPOSE 8765
CMD ["montivo", "serve", "--root", "/data", "--host", "0.0.0.0", "--port", "8765"]
