# The charts-toolbox image of signalk-charts-provider-simple (GDAL with Python
# bindings, numpy, tippecanoe), plus 7z for streaming survey XYZ and osmium
# for OpenStreetMap extracts.
FROM ghcr.io/dirkwa/signalk-charts-provider-simple/charts-toolbox:1.1.0@sha256:dcffcc16d2b5242d468f4eb340151acbb688225636d348fbfe6d100bea0179dd

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends 7zip osmium-tool python3-pytest \
 && rm -rf /var/lib/apt/lists/*
COPY odc /opt/odc/odc
ENV PYTHONPATH=/opt/odc
USER toolbox
