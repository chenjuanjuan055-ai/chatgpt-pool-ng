ARG NODE_VERSION=22
ARG GO_VERSION=1.24
ARG ALPINE_VERSION=3.23

# ---------------------------------------------------------------- frontend
FROM --platform=$BUILDPLATFORM node:${NODE_VERSION}-alpine AS frontend-builder

WORKDIR /src/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- backend
FROM --platform=$BUILDPLATFORM golang:${GO_VERSION}-alpine AS backend-builder

ARG TARGETOS
ARG TARGETARCH

WORKDIR /src/backend

COPY backend/go.mod ./
COPY backend/cmd ./cmd
COPY backend/internal ./internal

RUN --mount=type=cache,id=minimax2api-go-mod,target=/go/pkg/mod,sharing=locked \
    --mount=type=cache,id=minimax2api-go-build,target=/root/.cache/go-build,sharing=locked \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -buildvcs=false -trimpath -ldflags="-s -w" -o /out/minimax2api ./cmd/minimax2api

# ---------------------------------------------------------------- runtime
FROM alpine:${ALPINE_VERSION}

ENV TZ=Asia/Shanghai

RUN apk add --no-cache ca-certificates su-exec tzdata && \
    addgroup -S -g 10002 minimax2api && \
    adduser -S -D -H -u 10002 -G minimax2api minimax2api && \
    mkdir -p /app/data && \
    chown -R minimax2api:minimax2api /app/data

WORKDIR /app

COPY --from=backend-builder --chmod=0755 /out/minimax2api /app/minimax2api
COPY --from=frontend-builder /src/frontend/dist /app/frontend/dist

EXPOSE 8096

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8096/healthz >/dev/null || exit 1

ENTRYPOINT ["/app/minimax2api"]
CMD ["-addr", "0.0.0.0:8096", "-data", "data", "-static", "frontend/dist"]
