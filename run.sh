#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
echo "🚀 Photo Cleaner 启动中..."
echo "   打开浏览器访问: http://localhost:5800"
python app.py