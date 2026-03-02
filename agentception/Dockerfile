FROM python:3.11-slim

WORKDIR /app

# Install git + gh CLI (needed for subprocess calls to git/gh)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY agentception/requirements.txt /app/agentception/requirements.txt
RUN pip install --no-cache-dir -r /app/agentception/requirements.txt

COPY agentception/ /app/agentception/

CMD ["uvicorn", "agentception.app:app", "--host", "0.0.0.0", "--port", "7777"]
