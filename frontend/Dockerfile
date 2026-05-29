# Build context is frontend/ (this directory). Two-stage build: install + build
# the Next.js app, then run `next start` on Cloud Run.
#
# NEXT_PUBLIC_BFF_URL must be baked in AT BUILD TIME — it is inlined into the
# client bundle (the browser's EventSource + fetch calls read it), so passing it
# only as a runtime env var would not reach the client. We therefore take it as
# a build arg. Deploy via Cloud Build (see frontend/cloudbuild.yaml) which wires
# the arg, or build locally:
#   docker build --build-arg NEXT_PUBLIC_BFF_URL=https://<bff-cloud-run-url> -t claimdrift-frontend .
FROM node:22-slim AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG NEXT_PUBLIC_BFF_URL
ENV NEXT_PUBLIC_BFF_URL=${NEXT_PUBLIC_BFF_URL}
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production

# Copy only what `next start` needs at runtime.
COPY --from=builder /app/package.json /app/package-lock.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts ./next.config.ts

# Cloud Run injects $PORT (default 8080); `next start -p` honors it.
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "npm run start -- -p ${PORT} -H 0.0.0.0"]
