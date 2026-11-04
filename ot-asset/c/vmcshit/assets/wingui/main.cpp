
#include "gui_api.h"
#include <windows.h>
#include <memory>

class MyApp {
private:
    gui::ModernWindow m_window;
    
    
    gui::CheckBox* m_checkBox;
    gui::RadioButton* m_radio1;
    gui::RadioButton* m_radio2;
    gui::ToggleSwitch* m_toggle;
    gui::TextBox* m_textBox;
    
    
    gui::ComboBox* m_comboBox;
    gui::ListBox* m_listBox;
    gui::TabControl* m_tabControl;
    gui::Spinner* m_spinner;
    
    
    gui::RadioGroup m_radioGroup;
    
    
    gui::Button* m_statusButton;
    gui::ProgressBar* m_statusProgress;
    
public:
    bool Initialize(HINSTANCE hInstance, int nCmdShow) {
        gui::Initialize();
        
        if (!m_window.Initialize(hInstance, nCmdShow, L"Modern GUI Demo - Complete Suite", 950, 720)) {
            return false;
        }
        
        
        gui::Theme::ApplyDark(m_window);
        m_window.SetBackgroundGradient(
            gui::Color(0.05f, 0.05f, 0.12f, 1.0f),
            gui::Color(0.12f, 0.08f, 0.22f, 1.0f)
        );
        
        
        
        
        auto title1 = std::make_unique<gui::Button>();
        title1->SetBounds(gui::Rect(30, 20, 250, 50));
        title1->SetText(L"=== Basic Controls ===");
        title1->SetBackgroundColor(gui::Color(0, 0, 0, 0));
        title1->SetTextColor(gui::Color(0.7f, 0.7f, 0.9f, 1.0f));
        title1->SetBorderRadius(0);
        m_window.AddComponent(std::move(title1));
        
        
        
        
        auto checkBox = std::make_unique<gui::CheckBox>();
        checkBox->SetBounds(gui::Rect(30, 60, 180, 90));
        checkBox->SetText(L"Enable Feature");
        checkBox->SetCheckColor(gui::Color(0.2f, 0.7f, 1.0f, 1.0f));
        checkBox->OnChecked([](bool checked) {
            MessageBoxW(nullptr, checked ? L"Feature Enabled!" : L"Feature Disabled", L"CheckBox", MB_OK);
        });
        m_checkBox = checkBox.get();
        m_window.AddComponent(std::move(checkBox));
        
        
        
        
        auto radio1 = std::make_unique<gui::RadioButton>();
        radio1->SetBounds(gui::Rect(30, 100, 180, 130));
        radio1->SetText(L"Option A");
        radio1->SetGroupId(1);
        
        auto radio2 = std::make_unique<gui::RadioButton>();
        radio2->SetBounds(gui::Rect(30, 140, 180, 170));
        radio2->SetText(L"Option B");
        radio2->SetGroupId(1);
        
        m_radio1 = radio1.get();
        m_radio2 = radio2.get();
        m_radioGroup.Add(m_radio1);
        m_radioGroup.Add(m_radio2);
        m_radio1->SetSelected(true);
        
        m_radio1->OnSelected([](bool selected) {
            if (selected) MessageBoxW(nullptr, L"Option A Selected", L"Radio", MB_OK);
        });
        m_radio2->OnSelected([](bool selected) {
            if (selected) MessageBoxW(nullptr, L"Option B Selected", L"Radio", MB_OK);
        });
        
        m_window.AddComponent(std::move(radio1));
        m_window.AddComponent(std::move(radio2));
        
        
        
        
        auto toggle = std::make_unique<gui::ToggleSwitch>();
        toggle->SetBounds(gui::Rect(30, 185, 180, 220));
        toggle->SetOffColor(gui::Color(0.3f, 0.3f, 0.4f, 0.9f));
        toggle->SetOnColor(gui::Color(0.2f, 0.7f, 1.0f, 1.0f));
        toggle->OnToggle([](bool checked) {
            MessageBoxW(nullptr, checked ? L"Switch ON" : L"Switch OFF", L"Toggle", MB_OK);
        });
        m_toggle = toggle.get();
        m_window.AddComponent(std::move(toggle));
        
        
        
        
        auto textBox = std::make_unique<gui::TextBox>();
        textBox->SetBounds(gui::Rect(30, 235, 280, 270));
        textBox->SetPlaceholder(L"Type something here...");
        textBox->SetTextColor(gui::Color(0.95f, 0.95f, 1.0f, 1.0f));
        textBox->OnTextChanged([](const std::wstring& text) {
            
            wchar_t buf[256];
            swprintf_s(buf, L"Text: %s", text.c_str());
            SetWindowTextA(GetForegroundWindow(), "Text Changed");
        });
        m_textBox = textBox.get();
        m_window.AddComponent(std::move(textBox));
        
        
        
        
        auto title2 = std::make_unique<gui::Button>();
        title2->SetBounds(gui::Rect(320, 20, 540, 50));
        title2->SetText(L"=== Extended Controls ===");
        title2->SetBackgroundColor(gui::Color(0, 0, 0, 0));
        title2->SetTextColor(gui::Color(0.7f, 0.7f, 0.9f, 1.0f));
        title2->SetBorderRadius(0);
        m_window.AddComponent(std::move(title2));
        
        
        
        
        auto comboBox = std::make_unique<gui::ComboBox>();
        comboBox->SetBounds(gui::Rect(320, 60, 480, 95));
        comboBox->AddItem(L"Select an option...");
        comboBox->AddItem(L"Option 1 - Dark Theme");
        comboBox->AddItem(L"Option 2 - Light Theme");
        comboBox->AddItem(L"Option 3 - Blue Theme");
        comboBox->AddItem(L"Option 4 - Custom");
        comboBox->SetSelectedIndex(0);
        comboBox->OnSelectionChanged([this](int index, const std::wstring& text) {
            if (index == 1) gui::Theme::ApplyDark(m_window);
            else if (index == 2) gui::Theme::ApplyLight(m_window);
            else if (index == 3) m_window.SetBackgroundGradient(
                gui::Color(0.1f, 0.2f, 0.4f, 1.0f),
                gui::Color(0.05f, 0.1f, 0.3f, 1.0f));
            
            wchar_t buf[256];
            swprintf_s(buf, L"Selected: %s", text.c_str());
            MessageBoxW(nullptr, buf, L"ComboBox", MB_OK);
        });
        m_comboBox = comboBox.get();
        m_window.AddComponent(std::move(comboBox));
        
        
        
        
        auto listBox = std::make_unique<gui::ListBox>();
        listBox->SetBounds(gui::Rect(320, 100, 480, 220));
        listBox->AddItem(L"Item 1 - Apple");
        listBox->AddItem(L"Item 2 - Banana");
        listBox->AddItem(L"Item 3 - Cherry");
        listBox->AddItem(L"Item 4 - Date");
        listBox->AddItem(L"Item 5 - Elderberry");
        listBox->AddItem(L"Item 6 - Fig");
        listBox->AddItem(L"Item 7 - Grape");
        listBox->AddItem(L"Item 8 - Honeydew");
        listBox->SetItemHeight(26);
        listBox->OnSelectionChanged([](int index, const std::wstring& text) {
            wchar_t buf[256];
            swprintf_s(buf, L"Selected: %s", text.c_str());
            MessageBoxW(nullptr, buf, L"ListBox", MB_OK);
        });
        listBox->OnDoubleClick([](int index, const std::wstring& text) {
            wchar_t buf[256];
            swprintf_s(buf, L"Double-clicked: %s", text.c_str());
            MessageBoxW(nullptr, buf, L"ListBox Double Click", MB_OK);
        });
        m_listBox = listBox.get();
        m_window.AddComponent(std::move(listBox));
        
        
        
        
        auto spinner = std::make_unique<gui::Spinner>();
        spinner->SetBounds(gui::Rect(320, 235, 370, 270));
        spinner->SetRadius(20);
        spinner->SetColor(gui::Color(0.2f, 0.7f, 1.0f, 1.0f));
        spinner->SetSpeed(8.0f);
        spinner->Start();
        m_spinner = spinner.get();
        m_window.AddComponent(std::move(spinner));
        
        
        
        
        auto title3 = std::make_unique<gui::Button>();
        title3->SetBounds(gui::Rect(520, 20, 740, 50));
        title3->SetText(L"=== Tab Container ===");
        title3->SetBackgroundColor(gui::Color(0, 0, 0, 0));
        title3->SetTextColor(gui::Color(0.7f, 0.7f, 0.9f, 1.0f));
        title3->SetBorderRadius(0);
        m_window.AddComponent(std::move(title3));
        
        
        
        
        auto tabControl = std::make_unique<gui::TabControl>();
        tabControl->SetBounds(gui::Rect(520, 60, 900, 400));
        
        
        tabControl->AddTab(L"Settings");
        tabControl->AddTab(L"About");
        tabControl->AddTab(L"Help");
        
        
        auto settingsCheck = std::make_unique<gui::CheckBox>();
        settingsCheck->SetBounds(gui::Rect(540, 110, 700, 145));
        settingsCheck->SetText(L"Enable Auto-Save");
        tabControl->AddComponentToTab(0, std::move(settingsCheck));
        
        auto settingsToggle = std::make_unique<gui::ToggleSwitch>();
        settingsToggle->SetBounds(gui::Rect(540, 160, 700, 195));
        tabControl->AddComponentToTab(0, std::move(settingsToggle));
        
        
        auto aboutText = std::make_unique<gui::Button>();
        aboutText->SetBounds(gui::Rect(540, 110, 850, 200));
        aboutText->SetText(L"Modern GUI Library\nVersion 1.0\n\nBuilt with Direct2D\nSupports animations, themes, and rich controls");
        aboutText->SetBackgroundColor(gui::Color(0.15f, 0.15f, 0.22f, 1.0f));
        aboutText->SetTextColor(gui::Color(0.8f, 0.8f, 0.95f, 1.0f));
        aboutText->SetBorderRadius(8);
        tabControl->AddComponentToTab(1, std::move(aboutText));
        
        
        auto helpText = std::make_unique<gui::Button>();
        helpText->SetBounds(gui::Rect(540, 110, 850, 200));
        helpText->SetText(L"Controls:\n- Click buttons and cards\n- Toggle switches and checkboxes\n- Select from dropdowns\n- Type in text boxes\n- Double-click list items");
        helpText->SetBackgroundColor(gui::Color(0.15f, 0.15f, 0.22f, 1.0f));
        helpText->SetTextColor(gui::Color(0.8f, 0.8f, 0.95f, 1.0f));
        helpText->SetBorderRadius(8);
        tabControl->AddComponentToTab(2, std::move(helpText));
        
        tabControl->OnTabChanged([](int index, const std::wstring& title) {
            wchar_t buf[256];
            swprintf_s(buf, L"Switched to tab: %s", title.c_str());
            
        });
        
        m_tabControl = tabControl.get();
        m_window.AddComponent(std::move(tabControl));
        
        
        
        
        
        auto statusBtn = std::make_unique<gui::Button>();
        statusBtn->SetBounds(gui::Rect(30, 430, 200, 480));
        statusBtn->SetText(L"Show Status");
        statusBtn->SetBackgroundColor(gui::Color(0.2f, 0.55f, 0.9f, 1.0f));
        statusBtn->SetBorderRadius(25);
        statusBtn->OnClick([this]() {
            std::wstring msg = L"Status Report:\n\n";
            msg += L"CheckBox: " + std::wstring(m_checkBox->IsChecked() ? L"✓ Enabled" : L"✗ Disabled") + L"\n";
            msg += L"Radio: " + std::wstring(m_radio1->IsSelected() ? L"Option A" : L"Option B") + L"\n";
            msg += L"Toggle: " + std::wstring(m_toggle->IsChecked() ? L"ON" : L"OFF") + L"\n";
            msg += L"TextBox: " + m_textBox->GetText() + L"\n";
            msg += L"ComboBox: " + m_comboBox->GetSelectedText() + L"\n";
            
            int selIdx = m_listBox->GetSelectedIndex();
            msg += L"ListBox: " + (selIdx >= 0 ? std::wstring(L"Item ") + std::to_wstring(selIdx) : L"None");
            
            MessageBoxW(nullptr, msg.c_str(), L"Status", MB_OK);
        });
        m_statusButton = statusBtn.get();
        m_window.AddComponent(std::move(statusBtn));
        
        
        auto statusProgress = std::make_unique<gui::ProgressBar>();
        statusProgress->SetBounds(gui::Rect(30, 490, 280, 510));
        statusProgress->SetHeight(20);
        statusProgress->SetBorderRadius(10);
        statusProgress->SetProgressGradient(
            gui::Color(0.2f, 0.7f, 1.0f, 1.0f),
            gui::Color(0.5f, 0.4f, 0.9f, 1.0f)
        );
        statusProgress->SetValue(0.65f);
        statusProgress->SetShowPercentage(true);
        m_statusProgress = statusProgress.get();
        m_window.AddComponent(std::move(statusProgress));
        
        
        auto resetBtn = std::make_unique<gui::Button>();
        resetBtn->SetBounds(gui::Rect(30, 530, 200, 570));
        resetBtn->SetText(L"Reset All");
        resetBtn->SetBackgroundColor(gui::Color(0.4f, 0.3f, 0.5f, 1.0f));
        resetBtn->SetBorderRadius(25);
        resetBtn->OnClick([this]() {
            m_checkBox->SetChecked(false);
            m_radio1->SetSelected(true);
            m_toggle->SetChecked(false);
            m_textBox->SetText(L"");
            m_comboBox->SetSelectedIndex(0);
            m_statusProgress->SetValue(0.0f);
            m_statusProgress->SetTargetValue(0.65f, 1.0f);
            m_spinner->Start();
            
            MessageBoxW(nullptr, L"All controls reset!", L"Reset", MB_OK);
        });
        m_window.AddComponent(std::move(resetBtn));
        
        
        auto exitBtn = std::make_unique<gui::Button>();
        exitBtn->SetBounds(gui::Rect(30, 590, 200, 630));
        exitBtn->SetText(L"Exit");
        exitBtn->SetBackgroundColor(gui::Color(0.7f, 0.2f, 0.2f, 1.0f));
        exitBtn->SetBorderRadius(25);
        exitBtn->OnClick([this]() {
            if (gui::MessageBox::Show(&m_window, L"Exit", L"Are you sure you want to exit?",
                                       gui::MessageBoxButton::YesNo, gui::MessageBoxIcon::Question) == gui::MessageBoxResult::Yes) {
                PostQuitMessage(0);
            }
        });
        m_window.AddComponent(std::move(exitBtn));
        
        
        
        
        auto infoCard = std::make_unique<gui::Card>();
        infoCard->SetBounds(gui::Rect(310, 430, 550, 560));
        infoCard->SetTitle(L"Quick Guide");
        infoCard->SetSubtitle(L"Try all the controls above!\n\n"
                              L"• CheckBox: Toggle feature\n"
                              L"• Radio: Choose option\n"
                              L"• Toggle: Switch mode\n"
                              L"• TextBox: Type text\n"
                              L"• ComboBox: Select theme\n"
                              L"• ListBox: Click items\n"
                              L"• TabControl: Switch pages\n"
                              L"• Spinner: Loading animation");
        infoCard->SetElevation(6);
        infoCard->SetBackgroundColor(gui::Color(0.1f, 0.1f, 0.16f, 0.95f));
        m_window.AddComponent(std::move(infoCard));
        
        return true;
    }
    
    void Run() {
        m_window.Run();
    }
};

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE, LPSTR, int nCmdShow) {
    MyApp app;
    if (!app.Initialize(hInstance, nCmdShow)) {
        MessageBoxW(nullptr, L"Failed to initialize application!", L"Error", MB_ICONERROR);
        return 1;
    }
    app.Run();
    gui::Shutdown();
    return 0;
}