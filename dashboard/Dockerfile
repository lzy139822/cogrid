# Cogrid Dashboard Dockerfile
# 多阶段构建：先构建前端，再用 nginx 服务静态文件

# ===== 构建阶段 =====
FROM node:18-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN npm run build

# ===== 运行阶段 =====
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# nginx 配置：SPA 路由回退 + API 代理
RUN cat > /etc/nginx/conf.d/default.conf << 'EOF'
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # SPA 路由回退
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 代理到协调器
    location /api/ {
        proxy_pass http://coordinator:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
