# =============================================================================
# Cumulus MCP Prototype dev container
#
# One environment for both halves of the prototype:
#   - Flutter *web* admin app (admin_app/) — pinned SDK, Chromium, Firebase +
#     FlutterFire CLIs, deployed to Firebase Hosting
#   - Python API server (server/) — REST + MCP + A2A, deployed to Cloud Run.
#     Dependencies live in server/.venv, built by postCreateCommand from the
#     Miniforge base interpreter.
#
# The Miniforge base provides conda/Python; Flutter and the cloud CLIs are
# layered on top. Runs as root (the Miniforge image's standard pattern), which
# also keeps the host gcloud-config mount at /root/.config/gcloud simple.
# =============================================================================
FROM condaforge/miniforge3:latest

ENV DEBIAN_FRONTEND=noninteractive

# Pin the Flutter SDK so every developer builds with the same toolchain — keep
# in sync with the `sdk:` constraint in the app's pubspec.yaml.
ARG FLUTTER_VERSION=3.35.7
ARG NODE_MAJOR=22

# ---------------------------------------------------------------------------
# System packages.
#   xz-utils/zip/unzip   – Flutter SDK tooling
#   libglu1-mesa         – required by `flutter doctor`
#   chromium + fonts     – headless browser for web tests/debugging
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    curl \
    fonts-liberation \
    git \
    gnupg \
    jq \
    libglu1-mesa \
    make \
    openssh-client \
    unzip \
    xz-utils \
    zip \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Node.js + Firebase CLI (used for `flutterfire configure` and
# `firebase deploy --only hosting`).
# ---------------------------------------------------------------------------
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g firebase-tools && \
    npm cache clean --force && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Google Cloud CLI — authenticate with ADC from the host mount and deploy the
# MCP server with `gcloud run deploy --source server`.
# ---------------------------------------------------------------------------
RUN curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list && \
    apt-get update && apt-get install -y --no-install-recommends google-cloud-cli && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Flutter SDK — web artifacts only (no Android/iOS/desktop toolchains).
# ---------------------------------------------------------------------------
ENV FLUTTER_HOME=/usr/local/flutter
ENV PATH="${FLUTTER_HOME}/bin:${FLUTTER_HOME}/bin/cache/dart-sdk/bin:/root/.pub-cache/bin:${PATH}"
ENV CHROME_EXECUTABLE=/usr/bin/chromium

RUN git clone --depth 1 -b "${FLUTTER_VERSION}" \
    https://github.com/flutter/flutter.git "${FLUTTER_HOME}"

# Warm the SDK: Dart snapshot + web artifacts, and install the FlutterFire CLI.
RUN flutter --disable-analytics && \
    flutter precache --web --no-android --no-ios && \
    dart pub global activate flutterfire_cli && \
    flutter doctor -v

# Login shells source /etc/profile, which resets PATH — re-add Flutter/pub-cache.
RUN printf 'export PATH="%s"\n' \
    "${FLUTTER_HOME}/bin:${FLUTTER_HOME}/bin/cache/dart-sdk/bin:/root/.pub-cache/bin:\$PATH" \
    > /etc/profile.d/flutter.sh

# ---------------------------------------------------------------------------
# Python: the Miniforge base interpreter is the only one. postCreateCommand
# builds server/.venv from it, which is what VS Code, pytest, and the README
# all point at — one environment, not two.
#
# Auto-activate that venv in terminals once it exists.
# ---------------------------------------------------------------------------
RUN printf '%s\n' \
    '[ -f "$PWD/server/.venv/bin/activate" ] && . "$PWD/server/.venv/bin/activate"' \
    >> /etc/bash.bashrc

