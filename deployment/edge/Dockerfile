# Supply a verified NVIDIA ARM64/Python3.12/Orin CUDA image by immutable digest.
# There is deliberately no moving 'latest' default or CPU-torch replacement.
ARG JETSON_BASE_IMAGE
FROM ${JETSON_BASE_IMAGE}
ARG JETSON_BASE_IMAGE
ARG OPEN3D_WHEEL
ARG OPEN3D_WHEEL_MANIFEST
ENV AETHERTWIN_BASE_IMAGE=${JETSON_BASE_IMAGE} PYTHONUNBUFFERED=1
WORKDIR /opt/aethertwin
COPY . .
RUN python -c "import os,re; assert re.fullmatch(r'nvcr\.io/nvidia/.+@sha256:[0-9a-f]{64}',os.environ['AETHERTWIN_BASE_IMAGE']), 'Use the reviewed NVIDIA image digest'"
RUN python deployment/edge/open3d_wheel.py install --wheel "${OPEN3D_WHEEL}" --manifest "${OPEN3D_WHEEL_MANIFEST}"
RUN python -m pip freeze > /opt/vendor.constraints.txt
RUN python -m pip install --no-cache-dir -r deployment/edge/requirements-runtime.txt -c /opt/vendor.constraints.txt
RUN python native/voxel_rays/build.py
RUN python tools/probe_jetson.py --software-only --require-compatible --output /opt/aether-software-probe.json
ENTRYPOINT ["python", "deployment/edge/entrypoint.py"]
CMD ["--mode", "live", "--config", "config/profiles/jetson_edge.yaml", "--twin", "/data/twin", "--output", "/data/runtime"]
