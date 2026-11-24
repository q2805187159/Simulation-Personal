FROM golang:1.26.11-bookworm AS openmpp-build
COPY skeleton/openmpp /opt/skeleton-model
RUN sh /opt/skeleton-model/build.sh /opt/skeleton-openmpp
RUN curl -fsSL --retry 3 https://github.com/cue-lang/cue/releases/download/v0.17.1/cue_v0.17.1_linux_amd64.tar.gz -o /tmp/cue.tar.gz \
    && echo 'a39b0c97695069d95d276d99be0f5dbabb081d801bfdc9ba49b76efaf94e2369  /tmp/cue.tar.gz' | sha256sum -c - \
    && tar -xf /tmp/cue.tar.gz -C /usr/local/bin cue

FROM golang:1.26.11-bookworm AS minizinc-build
RUN curl -fsSL --retry 4 https://github.com/MiniZinc/libminizinc/releases/download/2.10.1/MiniZinc-2.10.1-x86_64-linux-gnu.tar.gz -o /tmp/minizinc.tar.gz \
    && echo 'ef7b97d7ce9bf848d2f7b5e412d4affb8f52167cb6fd0540c7c0a81a02940d1b  /tmp/minizinc.tar.gz' | sha256sum -c - \
    && mkdir /opt/minizinc \
    && tar -xzf /tmp/minizinc.tar.gz --strip-components=1 -C /opt/minizinc

FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=openmpp-build /opt/skeleton-openmpp /opt/skeleton-openmpp
COPY --from=openmpp-build /usr/local/bin/cue /usr/local/bin/cue
COPY --from=minizinc-build /opt/minizinc /opt/minizinc
ENV OPENMPP_MODEL=/opt/skeleton-openmpp/SkeletonCase CUE_BIN=/usr/local/bin/cue MINIZINC_BIN=/opt/minizinc/bin/minizinc
RUN "$MINIZINC_BIN" --version && "$MINIZINC_BIN" --solvers | grep -i gecode
RUN useradd --system app
COPY backend/app ./app
COPY ["docs/v3/维度字典.csv", "./app/data/dimension_catalog.csv"]
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
