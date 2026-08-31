# get_cookies.py
"""
Модуль для автоматической авторизации на data.ozon.ru с использованием Selenium и Gmail API.
Получает cookies для дальнейшего использования в парсере.
"""

import os
import re
import json
import time
import base64
import logging
from typing import Optional, Dict, List, Any
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ==================== НАСТРОЙКИ ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('auth.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('OzonAuth')

# Константы
PHONE_NUMBER = os.getenv('OZON_PHONE', '9232309252')
COOKIES_FILE = 'ozon_data_cookies.json'
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

# Время ожидания
WAIT_TIMEOUT = 15
CODE_WAIT_TIME = 25  # Увеличил время ожидания

# Прямой URL для авторизации
SSO_URL = 'https://sso.ozon.ru/auth/ozonid?redirect_uri=https%3A%2F%2Fdata.ozon.ru%2Fanalytics'


# ==================== GMAIL API ФУНКЦИИ ====================

def get_gmail_service():
    """Получение авторизованного сервиса Gmail API"""
    logger.info("📧 Подключение к Gmail...")

    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            pass

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Файл {CREDENTIALS_FILE} не найден. "
                    "Инструкция: https://developers.google.com/gmail/api/quickstart/python"
                )

            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def get_verification_code(service) -> Optional[str]:
    """
    Получение самого свежего кода подтверждения из Gmail.
    Ищет письма от разных адресов Ozon.
    """
    logger.info("🔍 Поиск самого свежего кода в Gmail...")

    try:
        # Пробуем разные поисковые запросы
        search_queries = [
            'from:(ozon.ru OR sender.ozon.ru OR mailer.ozon.ru) subject:(код OR подтверждение) newer_than:30m',
            'from:ozon.ru newer_than:30m',
            'from:sender.ozon.ru newer_than:30m',
            'from:mailer.ozon.ru newer_than:30m',
            'subject:(код OR подтверждение) newer_than:30m'
        ]

        messages = []
        for query in search_queries:
            try:
                result = service.users().messages().list(
                    userId='me',
                    q=query,
                    maxResults=10
                ).execute()
                msgs = result.get('messages', [])
                if msgs:
                    messages = msgs
                    logger.info(f"📬 Найдено {len(messages)} писем по запросу: {query}")
                    break
            except Exception:
                continue

        if not messages:
            # Если ничего не нашли, ищем все письма с кодом за последний час
            result = service.users().messages().list(
                userId='me',
                q='subject:(код OR подтверждение) newer_than:1h',
                maxResults=10
            ).execute()
            messages = result.get('messages', [])

        if not messages:
            raise Exception("Письма с кодом не найдены")

        logger.info(f"📬 Найдено {len(messages)} писем с кодом")

        # Собираем все коды с информацией о письмах
        codes_with_info = []

        for msg_data in messages:
            try:
                msg = service.users().messages().get(
                    userId='me',
                    id=msg_data['id'],
                    format='full'
                ).execute()

                # Извлекаем тему и отправителя
                subject = ''
                sender = ''
                for header in msg['payload'].get('headers', []):
                    if header['name'] == 'Subject':
                        subject = header['value']
                    if header['name'] == 'From':
                        sender = header['value']

                # Проверяем тему
                if 'подтвержд' not in subject.lower() and 'код' not in subject.lower():
                    continue

                # Извлекаем текст письма
                body_text = ""
                if 'parts' in msg['payload']:
                    for part in msg['payload'].get('parts', []):
                        if part['mimeType'] in ['text/plain', 'text/html']:
                            data = part['body'].get('data', '')
                            if data:
                                try:
                                    decoded = base64.urlsafe_b64decode(data)
                                    body_text += decoded.decode('utf-8', errors='ignore')
                                except Exception:
                                    pass
                else:
                    data = msg['payload']['body'].get('data', '')
                    if data:
                        try:
                            decoded = base64.urlsafe_b64decode(data)
                            body_text = decoded.decode('utf-8', errors='ignore')
                        except Exception:
                            pass

                if body_text:
                    # Ищем 6-значный код
                    code_match = re.search(r'\b(\d{6})\b', body_text)
                    if code_match:
                        code = code_match.group(1)
                        # Проверяем, что код не из старого письма (не 070707)
                        # Если код 070707 - пропускаем, ищем другой
                        if code == '070707':
                            logger.info(f"  ⚠️ Пропускаем старый код 070707")
                            continue

                        codes_with_info.append({
                            'code': code,
                            'id': msg_data['id'],
                            'subject': subject,
                            'sender': sender
                        })
                        logger.info(f"  ✅ Найден код {code} в письме: {subject}")

            except Exception as e:
                logger.debug(f"Ошибка обработки письма: {e}")
                continue

        # Если не нашли новых кодов, но есть старый - используем его
        if not codes_with_info:
            logger.warning("⚠️ Новых кодов не найдено, ищем все доступные...")

            # Ищем все коды без фильтрации
            for msg_data in messages:
                try:
                    msg = service.users().messages().get(
                        userId='me',
                        id=msg_data['id'],
                        format='full'
                    ).execute()

                    body_text = ""
                    if 'parts' in msg['payload']:
                        for part in msg['payload'].get('parts', []):
                            if part['mimeType'] in ['text/plain', 'text/html']:
                                data = part['body'].get('data', '')
                                if data:
                                    try:
                                        decoded = base64.urlsafe_b64decode(data)
                                        body_text += decoded.decode('utf-8', errors='ignore')
                                    except Exception:
                                        pass
                    else:
                        data = msg['payload']['body'].get('data', '')
                        if data:
                            try:
                                decoded = base64.urlsafe_b64decode(data)
                                body_text = decoded.decode('utf-8', errors='ignore')
                            except Exception:
                                pass

                    if body_text:
                        code_match = re.search(r'\b(\d{6})\b', body_text)
                        if code_match:
                            code = code_match.group(1)
                            codes_with_info.append({
                                'code': code,
                                'id': msg_data['id'],
                                'subject': 'Письмо от Ozon'
                            })
                            logger.info(f"  Найден код {code}")
                except Exception:
                    continue

        if not codes_with_info:
            raise Exception("Код не найден в письмах")

        # Берем первый код из списка (самый свежий)
        latest = codes_with_info[0]
        code = latest['code']

        logger.info(f"✅ Используем код: {code}")

        # Помечаем все письма как прочитанные
        for msg in codes_with_info:
            try:
                service.users().messages().modify(
                    userId='me',
                    id=msg['id'],
                    body={'removeLabelIds': ['UNREAD']}
                ).execute()
                logger.debug(f"Письмо с кодом {msg['code']} помечено как прочитанное")
            except Exception as e:
                logger.debug(f"Не удалось пометить письмо: {e}")

        return code

    except Exception as e:
        logger.error(f"❌ Ошибка получения кода: {e}")
        raise


# ==================== SELENIUM ФУНКЦИИ ====================

def setup_chrome_driver() -> webdriver.Chrome:
    """Настройка Chrome драйвера"""
    options = Options()

    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--start-maximized')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-popup-blocking')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-notifications')

    user_agent = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/131.0.0.0 Safari/537.36'
    )
    options.add_argument(f'user-agent={user_agent}')

    driver = webdriver.Chrome(options=options)

    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    return driver


def wait_for_element(driver, by: By, selector: str, timeout: int = WAIT_TIMEOUT):
    """Ожидание появления элемента"""
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, selector))
        )
    except TimeoutException:
        return None


def click_element(driver, by: By, selector: str, timeout: int = WAIT_TIMEOUT):
    """Ожидание и клик по элементу"""
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((by, selector))
        )
        element.click()
        return True
    except TimeoutException:
        return False


def input_phone_number(driver, phone: str) -> bool:
    """Ввод номера телефона"""
    logger.info(f"📱 Ввод номера: {phone}")

    time.sleep(2)

    phone_selectors = [
        "//input[@type='tel']",
        "//input[@name='phone']",
        "//input[@placeholder='Телефон']",
        "//input[contains(@class, 'phone')]"
    ]

    phone_input = None
    for selector in phone_selectors:
        phone_input = wait_for_element(driver, By.XPATH, selector, timeout=5)
        if phone_input and phone_input.is_displayed():
            break

    if not phone_input:
        logger.error("❌ Поле ввода не найдено")
        return False

    try:
        phone_input.clear()
        clean_phone = re.sub(r'[^\d]', '', phone)
        phone_input.send_keys(clean_phone)
        logger.info(f"✅ Номер введен")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return False


def submit_phone_form(driver) -> bool:
    """Отправка формы с номером телефона"""
    try:
        button_selectors = [
            "//button[contains(text(), 'Войти')]",
            "//button[contains(text(), 'Продолжить')]",
            "//button[contains(@type, 'submit')]",
            "//button[contains(@class, 'button-primary')]"
        ]

        for selector in button_selectors:
            if click_element(driver, By.XPATH, selector, timeout=3):
                logger.info("✅ Форма отправлена")
                return True

        # Пробуем через Enter
        try:
            phone_input = driver.find_element(By.XPATH, "//input[@type='tel']")
            phone_input.submit()
            logger.info("✅ Форма отправлена через Enter")
            return True
        except:
            pass

        return False

    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return False


def input_verification_code(driver, code: str) -> bool:
    """Ввод кода подтверждения"""
    logger.info(f"🔑 Ввод кода: {code}")

    # Ждем появления поля для кода
    time.sleep(3)

    # Ищем поле для ввода кода
    code_selectors = [
        "//input[@name='code']",
        "//input[@name='otp']",
        "//input[@type='text'][contains(@placeholder, 'код')]",
        "//input[@autocomplete='one-time-code']",
        "//input[contains(@id, 'code')]"
    ]

    code_input = None
    for selector in code_selectors:
        code_input = wait_for_element(driver, By.XPATH, selector, timeout=5)
        if code_input and code_input.is_displayed():
            break

    if not code_input:
        logger.warning("⚠️ Поле для кода не найдено")
        return False

    try:
        code_input.clear()
        code_input.send_keys(code)
        logger.info(f"✅ Код введен")

        time.sleep(1)

        # Ищем кнопку подтверждения
        confirm_selectors = [
            "//button[contains(text(), 'Подтвердить')]",
            "//button[contains(text(), 'Войти')]",
            "//button[contains(@type, 'submit')]"
        ]

        for selector in confirm_selectors:
            if click_element(driver, By.XPATH, selector, timeout=3):
                logger.info("✅ Код подтвержден")
                return True

        code_input.submit()
        logger.info("✅ Код подтвержден через Enter")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return False


def save_cookies(driver, filename: str) -> bool:
    """Сохранение cookies в файл"""
    logger.info("💾 Сохранение cookies...")

    try:
        cookies = driver.get_cookies()

        ozon_cookies = {}
        for cookie in cookies:
            if 'ozon' in cookie['domain'] or 'sso' in cookie['domain']:
                ozon_cookies[cookie['name']] = cookie['value']

        if not ozon_cookies:
            for cookie in cookies:
                ozon_cookies[cookie['name']] = cookie['value']

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(ozon_cookies, f, indent=2, ensure_ascii=False)

        logger.info(f"✅ Сохранено {len(ozon_cookies)} cookies")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return False


# ==================== ОСНОВНАЯ ФУНКЦИЯ ====================

def main():
    """Основная функция авторизации"""
    logger.info("=" * 60)
    logger.info("🚀 АВТОРИЗАЦИЯ НА DATA.OZON.RU")
    logger.info("=" * 60)

    driver = None

    try:
        # ШАГ 1: Настройка браузера и переход на SSO
        driver = setup_chrome_driver()

        logger.info("🔐 Переход на страницу авторизации...")
        driver.get(SSO_URL)
        time.sleep(3)

        # ШАГ 2: Ввод номера телефона
        if not input_phone_number(driver, PHONE_NUMBER):
            raise Exception("Не удалось ввести номер")

        if not submit_phone_form(driver):
            raise Exception("Не удалось отправить форму")

        # ШАГ 3: Получение кода
        logger.info(f"⏳ Ожидание кода ({CODE_WAIT_TIME} сек)...")
        time.sleep(CODE_WAIT_TIME)

        gmail_service = get_gmail_service()
        code = get_verification_code(gmail_service)

        # ШАГ 4: Ввод кода
        if not input_verification_code(driver, code):
            logger.warning("⚠️ Автоматический ввод не удался")
            logger.info("👤 Введите код вручную")
            time.sleep(15)

        # ШАГ 5: Ожидание и сохранение
        time.sleep(5)

        if save_cookies(driver, COOKIES_FILE):
            logger.info("=" * 60)
            logger.info("✅ АВТОРИЗАЦИЯ УСПЕШНО ЗАВЕРШЕНА!")
            logger.info("=" * 60)
        else:
            raise Exception("Не удалось сохранить cookies")

    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

    finally:
        if driver:
            driver.quit()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n🛑 Остановлено пользователем")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")