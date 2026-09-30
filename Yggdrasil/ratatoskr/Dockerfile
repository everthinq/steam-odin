# Node 22 LTS (Node 18 reached end of life in April 2025).
FROM node:22-alpine

WORKDIR /app

COPY package*.json ./

# Install exactly what package-lock.json pins; no development tools.
RUN npm ci --omit=dev

COPY . .

# Run as the image's unprivileged user; steam-user keeps its data in ~/.local/share.
USER node

EXPOSE 3000

CMD ["node", "server.js"]
