"""
Модуль автоматической авторизации на data.ozon.ru.
Интегрирует Selenium для эмуляции браузера и Gmail API для получения OTP-кодов.
Сохраняет валидные cookies для последующей работы парсера через requests.
"""

import os
import re
import json
import time
import base64
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException

# Gmail API
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ==================== КОНФИГУРАЦИЯ И ЛОГИРОВАНИЕ ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('auth.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('OzonAuth')

# Настройки аккаунта и путей
PHONE_NUMBER = os.getenv('OZON_PHONE', '9232309252')
COOKIES_FILE = 'ozon_data_cookies.json'
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

# Таймауты
WAIT_TIMEOUT = 15
CODE_WAIT_TIME = 30

# URL авторизации Ozon SSO
SSO_URL = 'https://sso.ozon.ru/auth/ozonid?redirect_uri=https%3A%2F%2Fdata.ozon.ru%2Fanalytics'

# Черный список тестовых/мусорных кодов
INVALID_CODES = {'070707', '000000', '111111', '999999', '123456'}


# ==================== GMAIL API: ПОЛУЧЕНИЕ КОДА ====================

def get_gmail_service():
    """Инициализация сервиса Gmail API с обновлением токенов."""
    logger.info("📧 Подключение к Gmail API...")
    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            logger.warning(f"⚠️ Ошибка чтения token.json: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("🔄 Токен Gmail успешно обновлен")
            except Exception as e:
                logger.error(f"❌ Не удалось обновить токен: {e}")
                creds = None

        if not creds:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Файл {CREDENTIALS_FILE} не найден! "
                    "Скачайте его из Google Cloud Console -> APIs & Services -> Credentials."
                )
            logger.info("🔐 Требуется авторизация в браузере...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            logger.info("✅ Авторизация Gmail пройдена")

        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def extract_code_from_body(body_text: str) -> Optional[str]:
    """
    Умный поиск кода подтверждения.
    Сначала ищет по контексту, затем fallback на любой валидный 6-значный код.
    """
    if not body_text:
        return None

    # Очищаем текст от лишних пробелов и переносов для надежности поиска
    clean_text = re.sub(r'\s+', ' ', body_text).strip()

    # 1. Поиск по ключевым фразам (более мягкие паттерны)
    patterns = [
        r'используйте\s+код[:\s]*(\d{6})',
        r'код\s+для\s+подтверждения[:\s]*(\d{6})',
        r'ваш\s+код[:\s]*(\d{6})',
        r'confirmation\s+code[:\s]*(\d{6})',
        r'код[:\s]*(\d{6})'  # Самый общий паттерн
    ]

    for pattern in patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            code = match.group(1)
            if code not in INVALID_CODES:
                return code

    # 2. Fallback: если контекст не найден, берем первый валидный 6-значный код
    all_codes = re.findall(r'\b(\d{6})\b', clean_text)
    for code in all_codes:
        if code not in INVALID_CODES:
            logger.debug(f"  Код найден через fallback: {code}")
            return code

    return None


def get_verification_code(service) -> str:
    """Получает самый свежий валидный код из Gmail."""
    logger.info(" Поиск кода подтверждения в почте...")

    query = 'from:(ozon.ru OR sender.ozon.ru OR mailer.ozon.ru) newer_than:2h'
    result = service.users().messages().list(userId='me', q=query, maxResults=20).execute()
    messages = result.get('messages', [])

    if not messages:
        raise Exception("Письма от Ozon не найдены за последние 2 часа")

    logger.info(f"📬 Найдено {len(messages)} писем. Анализ содержимого...")
    valid_codes = []

    for msg_data in messages:
        try:
            msg = service.users().messages().get(userId='me', id=msg_data['id'], format='full').execute()
            internal_date = int(msg.get('internalDate', 0))

            # Парсинг тела письма
            body_text = ""
            payload = msg['payload']
            if 'parts' in payload:
                for part in payload['parts']:
                    if part.get('mimeType') in ['text/plain', 'text/html']:
                        data = part['body'].get('data', '')
                        if data:
                            try:
                                decoded = base64.urlsafe_b64decode(data)
                                body_text += decoded.decode('utf-8', errors='ignore')
                            except Exception:
                                pass
            else:
                data = payload['body'].get('data', '')
                if data:
                    try:
                        decoded = base64.urlsafe_b64decode(data)
                        body_text = decoded.decode('utf-8', errors='ignore')
                    except Exception:
                        pass

            code = extract_code_from_body(body_text)
            if code:
                valid_codes.append({'code': code, 'id': msg_data['id'], 'date': internal_date})
                logger.debug(
                    f"  ✅ Код {code} найден в письме от {datetime.fromtimestamp(internal_date / 1000).strftime('%H:%M:%S')}")

        except Exception as e:
            logger.debug(f"⚠️ Ошибка обработки письма {msg_data['id']}: {e}")
            continue

    if not valid_codes:
        raise Exception("Не удалось найти валидный код подтверждения в письмах")

    # Сортировка по дате (самые свежие первыми)
    valid_codes.sort(key=lambda x: x['date'], reverse=True)

    latest = valid_codes[0]
    code = latest['code']
    time_str = datetime.fromtimestamp(latest['date'] / 1000).strftime('%H:%M:%S')

    logger.info(f"✅ Используем актуальный код: {code} (получен в {time_str})")

    # Помечаем письмо как прочитанное
    try:
        service.users().messages().modify(userId='me', id=latest['id'], body={'removeLabelIds': ['UNREAD']}).execute()
    except Exception:
        pass

    return code


# ==================== SELENIUM: АВТОМАТИЗАЦИЯ БРАУЗЕРА ====================

def setup_chrome_driver() -> webdriver.Chrome:
    """Настройка ChromeDriver с анти-детект параметрами."""
    options = Options()
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--start-maximized')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-popup-blocking')

    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    options.add_argument(f'user-agent={user_agent}')

    driver = webdriver.Chrome(options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def input_phone_number(driver, phone: str) -> bool:
    """Ввод номера телефона в форму авторизации."""
    logger.info(f"📱 Ввод номера: {phone}")
    time.sleep(2)

    selectors = [
        "//input[@type='tel']",
        "//input[@name='phone']",
        "//input[contains(@placeholder, 'Телефон')]"
    ]

    for sel in selectors:
        try:
            el = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, sel)))
            if el.is_displayed():
                el.clear()
                el.send_keys(re.sub(r'[^\d]', '', phone))
                logger.info("✅ Номер введен")
                return True
        except TimeoutException:
            continue

    logger.error("❌ Поле ввода телефона не найдено")
    return False


def submit_form(driver) -> bool:
    """Отправка формы с увеличенным ожиданием активации кнопки."""
    logger.info("🔘 Поиск и нажатие кнопки отправки...")

    btn_selectors = [
        "//button[contains(text(), 'Продолжить')]",
        "//button[contains(text(), 'Войти')]",
        "//button[@type='submit']",
        "//button[contains(@class, 'Button') and contains(@class, 'primary')]"
    ]

    for sel in btn_selectors:
        try:
            btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, sel)))

            # Проверка атрибута disabled
            is_disabled = btn.get_attribute('disabled')
            if is_disabled:
                logger.debug("Кнопка найдена, но имеет атрибут disabled. Ждем...")
                WebDriverWait(driver, 5).until_not(
                    lambda d: d.find_element(By.XPATH, sel).get_attribute('disabled')
                )

            btn.click()
            logger.info("✅ Форма отправлена (клик)")
            return True

        except TimeoutException:
            logger.debug(f"Кнопка '{sel}' не стала кликабельной за 10 сек")
            continue
        except Exception as e:
            logger.debug(f"Ошибка при клике на '{sel}': {e}")
            continue

    # Фолбэк: попытка отправить через Enter
    try:
        logger.info("️ Кнопка не найдена, пробуем Enter...")
        phone_input = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='tel']"))
        )
        phone_input.click()
        time.sleep(0.5)
        from selenium.webdriver.common.keys import Keys
        phone_input.send_keys(Keys.ENTER)
        logger.info("✅ Форма отправлена через Enter")
        return True
    except Exception as e:
        logger.error(f"❌ Не удалось отправить форму: {e}")
        return False


def input_verification_code(driver, code: str) -> bool:
    """
    Ввод OTP кода с поддержкой кастомных полей Ozon ID.
    Использует JS-фолбэк, если send_keys не срабатывает.
    """
    logger.info(f"🔑 Ввод кода: {code}")
    time.sleep(5)

    # Селекторы для поиска поля ввода
    code_selectors = [
        "//input[@placeholder='------']",
        "//input[@data-test-id='input-otp']",
        "//input[@autocomplete='one-time-code']",
        "//input[@type='text']",
        "//input[@type='tel']"
    ]

    code_input = None
    for selector in code_selectors:
        try:
            element = WebDriverWait(driver, 2).until(EC.presence_of_element_located((By.XPATH, selector)))
            if element and element.is_displayed() and element.is_enabled():
                code_input = element
                logger.info(f"✅ Найдено поле ввода")
                break
        except TimeoutException:
            continue

    if not code_input:
        logger.error("❌ Поле для кода не найдено!")
        logger.info(f"👤 ВВЕДИТЕ КОД ВРУЧНУЮ: {code}")
        time.sleep(30)
        return True

    try:
        # Фокус и прокрутка
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", code_input)
        time.sleep(0.5)
        code_input.click()
        time.sleep(0.5)

        # Ввод кода
        code_input.clear()
        code_input.send_keys(code)

        # Проверка значения и JS-фолбэк если нужно
        current_value = code_input.get_attribute('value')
        if current_value != code:
            logger.warning("️ send_keys не сработал, пробуем JavaScript...")
            driver.execute_script(f"arguments[0].value = '{code}';", code_input)
            driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles: true}));", code_input)
            driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", code_input)

        logger.info("✅ Код успешно введен")
        time.sleep(1)

        # Поиск кнопки подтверждения
        confirm_selectors = [
            "//button[contains(text(), 'Подтвердить')]",
            "//button[@type='submit']"
        ]

        for sel in confirm_selectors:
            try:
                btn = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((By.XPATH, sel)))
                btn.click()
                logger.info("✅ Код подтвержден кнопкой")
                return True
            except TimeoutException:
                continue

        logger.info("️ Кнопка не найдена, ожидаем автоматическую обработку...")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка ввода кода: {e}")
        return False


def save_cookies(driver, filename: str) -> bool:
    """Сохранение cookies, относящихся к домену Ozon."""
    logger.info("💾 Сохранение cookies...")
    try:
        all_cookies = driver.get_cookies()
        ozon_cookies = {}

        for cookie in all_cookies:
            domain = cookie.get('domain', '')
            if 'ozon' in domain or 'sso' in domain:
                ozon_cookies[cookie['name']] = cookie['value']

        if not ozon_cookies:
            for c in all_cookies:
                ozon_cookies[c['name']] = c['value']

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(ozon_cookies, f, indent=2, ensure_ascii=False)

        logger.info(f"✅ Сохранено {len(ozon_cookies)} cookies в {filename}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения cookies: {e}")
        return False


# ==================== ГЛАВНЫЙ ПРОЦЕСС АВТОРИЗАЦИИ ====================

def main():
    """Основной поток авторизации."""
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК АВТОРИЗАЦИИ НА DATA.OZON.RU")
    logger.info("=" * 60)

    driver = None
    try:
        # 1. Инициализация браузера
        driver = setup_chrome_driver()
        logger.info("🔐 Переход на страницу SSO Ozon...")
        driver.get(SSO_URL)
        time.sleep(3)

        # 2. Ввод телефона
        if not input_phone_number(driver, PHONE_NUMBER):
            raise Exception("Ошибка ввода номера телефона")

        if not submit_form(driver):
            raise Exception("Не удалось отправить номер телефона")

        # 3. Ожидание и получение кода из Gmail
        logger.info(f" Ожидание SMS ({CODE_WAIT_TIME} сек)...")
        time.sleep(CODE_WAIT_TIME)

        gmail_service = get_gmail_service()
        verification_code = get_verification_code(gmail_service)

        # 4. Ввод кода в браузер
        if not input_verification_code(driver, verification_code):
            logger.warning("⚠️ Авто-ввод не сработал. У вас есть 15 сек на ручной ввод.")
            time.sleep(15)

        # 5. Финализация и сохранение сессии
        time.sleep(5)

        if save_cookies(driver, COOKIES_FILE):
            logger.info("=" * 60)
            logger.info(" АВТОРИЗАЦИЯ УСПЕШНО ЗАВЕРШЕНА!")
            logger.info("=" * 60)
        else:
            raise Exception("Cookies не были сохранены")

    except Exception as e:
        logger.error(f" Критическая ошибка: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()
            logger.info("🔄 Браузер закрыт")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n🛑 Процесс остановлен пользователем")