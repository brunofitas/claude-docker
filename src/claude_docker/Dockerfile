FROM node:22-slim

ARG USER_UID=1000
ARG USER_GID=1000

RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN groupadd -g ${USER_GID} claude || true \
    && useradd -m -s /bin/bash -u ${USER_UID} -g ${USER_GID} claude

# Create a wrapper that suppresses the npm-to-native warning
RUN printf '#!/bin/bash\nexec /usr/local/bin/claude "$@" 2> >(grep -v "switched from npm to native" >&2)\n' > /usr/local/bin/claude-wrapper \
    && chmod +x /usr/local/bin/claude-wrapper

USER claude
WORKDIR /workspace

ENTRYPOINT ["claude-wrapper"]
