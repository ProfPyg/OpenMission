# OpenMission — Streamlit app for HF Spaces (Docker SDK)
# Python 3.12: has prebuilt wheels for every pinned dependency, including
# the nrlmsise00 C extension, so no in-container compilation is needed.
FROM python:3.12-slim

# System libs: build-essential + libc6-dev are a safety net in case any wheel
# is missing for 3.12 and pip must compile from source (notably nrlmsise00).
# Removing the apt cache afterward keeps the image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces runs the container as a non-root user with UID 1000.
# Create that user and a home dir so caches (Skyfield, Streamlit) are writable.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    # Skyfield/HF library cache lives in the writable home dir
    HF_HOME=/home/user/.cache/huggingface \
    # Streamlit: no telemetry, no usage stats prompt, headless server
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true

WORKDIR /home/user/app

# Install dependencies first (layer caching: deps only re-install when
# requirements.txt changes, not on every code edit).
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy the application (code, pages/, data files, emblem assets, etc.)
COPY --chown=user . .

# Drop to the non-root user for runtime
USER user

# HF Spaces expects the app on port 7860
EXPOSE 7860

# Launch. Adjust "app.py" if your entrypoint file is named differently.
CMD ["streamlit", "run", "app.py", \
     "--server.port=7860", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false"]
