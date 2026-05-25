FROM oven/bun:1.3.13-alpine AS build

WORKDIR /app
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL

COPY package.json bun.lock ./
RUN bun install

COPY . .
RUN bun run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
