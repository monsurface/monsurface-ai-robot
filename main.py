from flask import Flask
import gspread
import requests
import os
from google.oauth2.service_account import Credentials
from rapidfuzz import process

app = Flask(__name__)

# ✅ 讀取環境變數
DROPBOX_URL = os.getenv("DROPBOX_URL")

# ✅ 各品牌對應 Google Sheet ID
BRAND_SHEETS = {
    "富美家": os.getenv("SPREADSHEET_ID_A"),
    "愛卡AICA-愛克板": os.getenv("SPREADSHEET_ID_D"),
}

# ✅ 下載 Google API 憑證
LOCAL_FILE_PATH = "credentials.json"

def download_credentials():
    """從 Dropbox 下載 credentials.json"""
    response = requests.get(DROPBOX_URL)
    if response.status_code == 200:
        with open(LOCAL_FILE_PATH, "wb") as file:
            file.write(response.content)
        print("✅ credentials.json 下載成功！")
    else:
        raise FileNotFoundError(f"❌ 下載失敗，HTTP 狀態碼: {response.status_code}")

# ✅ 嘗試下載憑證
download_credentials()

# ✅ 讀取 Google Sheets API 憑證
credentials = Credentials.from_service_account_file(
    LOCAL_FILE_PATH,
    scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
)
client = gspread.authorize(credentials)

def fuzzy_match_brand(user_input):
    """嘗試找到最接近的品牌名稱"""
    brand_match, score = process.extractOne(user_input, BRAND_SHEETS.keys())
    if score >= 80:
        print(f"🔍 匹配品牌成功：{brand_match}（匹配度：{score}）")
        return brand_match
    else:
        print(f"⚠️ 未找到匹配的品牌（最高匹配度：{score}）")
        return None

def get_sheets_data(brand):
    """根據品牌讀取對應的 Google Sheets 數據"""
    sheet_id = BRAND_SHEETS.get(brand)
    if not sheet_id:
        print(f"⚠️ 品牌 {brand} 沒有對應的 Google Sheets ID")
        return None

    try:
        spreadsheet = client.open_by_key(sheet_id)
        all_data = {}

        for sheet in spreadsheet.worksheets():
            sheet_name = sheet.title
            print(f"📂 讀取分頁：{sheet_name}")

            try:
                data = sheet.get_all_records(expected_headers=[])
                if not data:
                    print(f"⚠️ {sheet_name} 分頁是空的，跳過處理。")
                    continue
                all_data[sheet_name] = data
            except Exception as e:
                print(f"❌ 讀取 {sheet_name} 分頁時發生錯誤：{e}")
                continue  

        if all_data:
            print("✅ 成功讀取 Google Sheets！")
            return all_data
        else:
            print("⚠️ 該品牌的 Google Sheets 沒有可用數據")
            return None

    except Exception as e:
        print(f"❌ 讀取 Google Sheets 失敗：{e}")
        return None

# ✅ **測試**
if __name__ == "__main__":
    print("🚀 開始測試 Google Sheets 讀取功能...\n")

    # **測試 1：測試憑證是否下載**
    if os.path.exists(LOCAL_FILE_PATH):
        print("✅ `credentials.json` 存在！")
    else:
        print("❌ `credentials.json` 不存在，請檢查 Dropbox 連結。")

    # **測試 2：測試品牌匹配**
    test_brand = "富美"
    matched_brand = fuzzy_match_brand(test_brand)
    if matched_brand:
        print(f"🔍 測試品牌匹配結果：{matched_brand}\n")
    else:
        print("⚠️ 品牌匹配測試失敗\n")

    # **測試 3：測試讀取 Google Sheets**
    if matched_brand:
        print(f"📖 嘗試讀取品牌 `{matched_brand}` 的 Google Sheets...\n")
        sheets_data = get_sheets_data(matched_brand)
        if sheets_data:
            for sheet_name, data in sheets_data.items():
                print(f"\n📂 **{sheet_name}**（前 2 筆資料）：")
                for row in data[:2]:  # 只顯示前兩筆測試
                    print(row)
        else:
            print("❌ 讀取 Google Sheets 失敗\n")
    else:
        print("⚠️ 未匹配到品牌，無法測試 Google Sheets 讀取\n")

    print("✅ 測試完成！")
