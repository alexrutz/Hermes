FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .
RUN npm run build


FROM nginx:1.27-alpine

RUN apk add --no-cache curl

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD curl -fsS http://localhost/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
