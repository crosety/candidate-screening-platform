FROM node:24-slim
RUN npm install -g pnpm@12.10.1
WORKDIR /app
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY apps/console/package.json apps/console/
COPY apps/chat/package.json apps/chat/
COPY packages/api-client/package.json packages/api-client/
RUN pnpm install --frozen-lockfile
COPY . .
