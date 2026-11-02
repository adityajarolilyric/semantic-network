FROM node:24-alpine AS build
WORKDIR /application
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.28-alpine
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /application/dist /usr/share/nginx/html
EXPOSE 80
