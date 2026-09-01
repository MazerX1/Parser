# 🛒 Ozon Product Parser

Парсер карточек товаров с Ozon (ozon.ru) с авторизацией через cookies.

## ✨ Возможности

- 🔐 Авторизация через cookies
- 📊 Парсинг по списку SKU
- 📁 Сохранение в CSV и JSON
- 📝 Детальное логирование

### Парсимые поля
`sku`, `title`, `price`, `rating`, `reviews_total`, `cover_image`, `photos_seller`, `videos_seller`, `color`, `material`, `art_set`, `has_rich_content`

## 📦 Требования

- Python 3.9+
- Chrome браузер 120+
- ChromeDriver (автоматически через webdriver-manager)

## 🚀 Быстрый старт

### 1. Установка

```bash
# Клонирование
git clone https://github.com/MazerX1/Parser.git
cd Parser

# Виртуальное окружение
python -m venv .venv
source .venv/bin/activate  # или .venv\Scripts\activate

# Установка зависимостей
pip install -r requirements.txt
