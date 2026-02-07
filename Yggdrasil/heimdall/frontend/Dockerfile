FROM node:22-alpine

WORKDIR /app

# Copy package files first to leverage cache
COPY package.json package-lock.json ./

# Install dependencies
RUN npm install

# Copy the rest of the code
COPY . .

# Expose Vite port
EXPOSE 5173

# Start development server
CMD ["npm", "run", "dev", "--", "--host"]
