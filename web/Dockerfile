FROM node:22-alpine

RUN corepack enable && corepack prepare pnpm@11.7.0 --activate

WORKDIR /srv




COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

EXPOSE 3000
CMD ["pnpm", "dev"]
