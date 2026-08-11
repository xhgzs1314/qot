#include "gui_module.h"
#include "builtin.h"

namespace qot
{

    Value builtin_vg_init(const std::vector<Value> &args)
    {
        bool result = gui::Initialize();
        if (result)
        {
            GUIManager::instance().setInitialized(true);
        }
        return Value(result);
    }

    Value builtin_vg_uninit(const std::vector<Value> &args)
    {
        gui::Shutdown();
        GUIManager::instance().setInitialized(false);
        return Value(true);
    }

    Value builtin_vg_create_window(const std::vector<Value> &args)
    {
        if (!GUIManager::instance().isInitialized())
        {
            std::cerr << "[Error] GUI not initialized. Call vg_init() first." << std::endl;
            return Value(nullptr);
        }
        int width = args.size() > 1 ? static_cast<int>(args[1].asNumber()) : 800;
        int height = args.size() > 2 ? static_cast<int>(args[2].asNumber()) : 600;

        std::string title = args.size() > 0 ? args[0].toString() : "QOTGUITEST";
        std::wstring wtitle = gui_stringToWString(title);

        auto *window = new gui::ModernWindow();
        HINSTANCE hInstance = GetModuleHandle(nullptr);

        if (!window->Initialize(hInstance, SW_SHOW, wtitle, width, height))
        {
            delete window;
            return Value(nullptr);
        }

        GUIManager::instance().setMainWindow(window);
        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::Window, window);

        return Value(objId);
    }

    Value builtin_vg_show_window(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(false);

        ShowWindow(window->GetHwnd(), SW_SHOW);
        UpdateWindow(window->GetHwnd());
        return Value(true);
    }

    Value builtin_vg_run(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(false);

        window->Run();
        return Value(true);
    }

    Value builtin_vg_close_window(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(false);

        DestroyWindow(window->GetHwnd());
        delete window;
        GUIManager::instance().setMainWindow(nullptr);
        return Value(true);
    }

    Value builtin_vg_set_window_title(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window || args.empty())
            return Value(false);

        std::string title = args[0].toString();
        window->SetTitle(gui_stringToWString(title));
        return Value(true);
    }

    Value builtin_vg_set_window_size(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window || args.size() < 2)
            return Value(false);

        int width = gui_valueToInt(args[0]);
        int height = gui_valueToInt(args[1]);

        SetWindowPos(window->GetHwnd(), nullptr, 0, 0, width, height,
                     SWP_NOMOVE | SWP_NOZORDER);
        return Value(true);
    }

    Value builtin_vg_set_window_bg_gradient(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window || args.size() < 2)
            return Value(false);

        gui::Color start = gui_valueToColor(args[0]);
        gui::Color end = gui_valueToColor(args[1]);
        window->SetBackgroundGradient(start, end);
        return Value(true);
    }

    Value builtin_vg_create_button(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
        {
            std::cerr << "[Error] No window created. Call vg_create_window() first." << std::endl;
            return Value(nullptr);
        }

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 200;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 40;
        std::string text = args.size() > 4 ? args[4].toString() : "Button";

        auto button = std::make_unique<gui::Button>();
        button->SetBounds(gui::Rect(x, y, x + w, y + h));
        button->SetText(gui_stringToWString(text));

        gui::Button *buttonPtr = button.get();
        window->AddComponent(std::move(button));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::Button, buttonPtr);

        return Value(objId);
    }

    Value builtin_vg_button_set_text(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        std::string text = args[1].toString();

        auto *button = GUIManager::instance().getTyped<gui::Button>(objId, GUIObject::Type::Button);
        if (!button)
            return Value(false);

        button->SetText(gui_stringToWString(text));
        return Value(true);
    }

    Value builtin_vg_button_set_color(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        gui::Color color = gui_valueToColor(args[1]);

        auto *button = GUIManager::instance().getTyped<gui::Button>(objId, GUIObject::Type::Button);
        if (!button)
            return Value(false);

        button->SetBackgroundColor(color);
        return Value(true);
    }

    Value builtin_vg_button_set_hover_color(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        gui::Color color = gui_valueToColor(args[1]);

        auto *button = GUIManager::instance().getTyped<gui::Button>(objId, GUIObject::Type::Button);
        if (!button)
            return Value(false);

        button->SetHoverColor(color);
        return Value(true);
    }

    Value builtin_vg_button_set_border_radius(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        float radius = gui_valueToFloat(args[1]);

        auto *button = GUIManager::instance().getTyped<gui::Button>(objId, GUIObject::Type::Button);
        if (!button)
            return Value(false);

        button->SetBorderRadius(radius);
        return Value(true);
    }

    Value builtin_vg_button_on_click(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);
        std::string objId = args[0].toString();
        GUIBinding binding;
        if (!gui_parseCallback(args, binding))
        {
            std::cerr << "[Error] Invalid callback function" << std::endl;
            return Value(false);
        }

        auto *button = GUIManager::instance().getTyped<gui::Button>(objId, GUIObject::Type::Button);
        if (!button)
            return Value(false);

        GUIManager::instance().registerButtonCallback(objId, binding.fn, std::move(binding.bound));
        button->OnClick([objId]()
                        { GUIManager::instance().triggerButtonClick(objId); });

        return Value(true);
    }

    Value builtin_vg_button_set_position(const std::vector<Value> &args)
    {
        if (args.size() < 3)
            return Value(false);

        std::string objId = args[0].toString();
        float x = gui_valueToFloat(args[1]);
        float y = gui_valueToFloat(args[2]);

        auto *button = GUIManager::instance().getTyped<gui::Button>(objId, GUIObject::Type::Button);
        if (!button)
            return Value(false);

        gui::Rect bounds = button->GetBounds();
        float w = bounds.Width();
        float h = bounds.Height();
        button->SetBounds(gui::Rect(x, y, x + w, y + h));
        return Value(true);
    }

    Value builtin_vg_create_checkbox(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 150;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 30;
        std::string text = args.size() > 4 ? args[4].toString() : "CheckBox";

        auto checkbox = std::make_unique<gui::CheckBox>();
        checkbox->SetBounds(gui::Rect(x, y, x + w, y + h));
        checkbox->SetText(gui_stringToWString(text));

        gui::CheckBox *checkboxPtr = checkbox.get();
        window->AddComponent(std::move(checkbox));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::CheckBox, checkboxPtr);

        return Value(objId);
    }

    Value builtin_vg_checkbox_set_checked(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        bool checked = gui_valueToBool(args[1]);

        auto *checkbox = GUIManager::instance().getTyped<gui::CheckBox>(objId, GUIObject::Type::CheckBox);
        if (!checkbox)
            return Value(false);

        checkbox->SetChecked(checked);
        return Value(true);
    }

    Value builtin_vg_checkbox_get_checked(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(false);

        std::string objId = args[0].toString();
        auto *checkbox = GUIManager::instance().getTyped<gui::CheckBox>(objId, GUIObject::Type::CheckBox);
        if (!checkbox)
            return Value(false);

        return Value(checkbox->IsChecked());
    }

    Value builtin_vg_checkbox_on_changed(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        GUIBinding binding;
        if (!gui_parseCallback(args, binding))
        {
            std::cerr << "[Error] Invalid callback function for checkbox" << std::endl;
            return Value(false);
        }

        auto *checkbox = GUIManager::instance().getTyped<gui::CheckBox>(objId, GUIObject::Type::CheckBox);
        if (!checkbox)
            return Value(false);

        GUIManager::instance().registerCheckboxCallback(objId, binding.fn, std::move(binding.bound));
        checkbox->OnChecked([objId](bool checked)
                            { GUIManager::instance().triggerCheckboxChange(objId, checked); });

        return Value(true);
    }

    Value builtin_vg_create_toggle(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;

        auto toggle = std::make_unique<gui::ToggleSwitch>();
        toggle->SetBounds(gui::Rect(x, y, x + 60, y + 30));

        gui::ToggleSwitch *togglePtr = toggle.get();
        window->AddComponent(std::move(toggle));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::ToggleSwitch, togglePtr);

        return Value(objId);
    }

    Value builtin_vg_toggle_set_checked(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        bool checked = gui_valueToBool(args[1]);

        auto *toggle = GUIManager::instance().getTyped<gui::ToggleSwitch>(objId, GUIObject::Type::ToggleSwitch);
        if (!toggle)
            return Value(false);

        toggle->SetChecked(checked);
        return Value(true);
    }

    Value builtin_vg_toggle_get_checked(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(false);

        std::string objId = args[0].toString();
        auto *toggle = GUIManager::instance().getTyped<gui::ToggleSwitch>(objId, GUIObject::Type::ToggleSwitch);
        if (!toggle)
            return Value(false);

        return Value(toggle->IsChecked());
    }

    Value builtin_vg_toggle_on_changed(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        GUIBinding binding;
        if (!gui_parseCallback(args, binding))
        {
            std::cerr << "[Error] Invalid callback function for toggle" << std::endl;
            return Value(false);
        }

        auto *toggle = GUIManager::instance().getTyped<gui::ToggleSwitch>(objId, GUIObject::Type::ToggleSwitch);
        if (!toggle)
            return Value(false);

        GUIManager::instance().registerToggleCallback(objId, binding.fn, std::move(binding.bound));
        toggle->OnToggle([objId](bool checked)
                         { GUIManager::instance().triggerToggleChange(objId, checked); });

        return Value(true);
    }

    Value builtin_vg_create_textbox(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 200;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 35;
        std::string placeholder = args.size() > 4 ? args[4].toString() : "Enter text...";

        auto textbox = std::make_unique<gui::TextBox>();
        textbox->SetBounds(gui::Rect(x, y, x + w, y + h));
        textbox->SetPlaceholder(gui_stringToWString(placeholder));
        textbox->SetRenderer(window->GetRenderer());

        gui::TextBox *textboxPtr = textbox.get();
        window->AddComponent(std::move(textbox));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::TextBox, textboxPtr);

        return Value(objId);
    }

    Value builtin_vg_textbox_set_text(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        std::string text = args[1].toString();

        auto *textbox = GUIManager::instance().getTyped<gui::TextBox>(objId, GUIObject::Type::TextBox);
        if (!textbox)
            return Value(false);

        textbox->SetText(gui_stringToWString(text));
        return Value(true);
    }

    Value builtin_vg_textbox_get_text(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value("");

        std::string objId = args[0].toString();
        auto *textbox = GUIManager::instance().getTyped<gui::TextBox>(objId, GUIObject::Type::TextBox);
        if (!textbox)
            return Value("");

        std::wstring wtext = textbox->GetText();
        return Value(gui_wstringToString(wtext));
    }

    Value builtin_vg_textbox_set_placeholder(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        std::string placeholder = args[1].toString();

        auto *textbox = GUIManager::instance().getTyped<gui::TextBox>(objId, GUIObject::Type::TextBox);
        if (!textbox)
            return Value(false);

        textbox->SetPlaceholder(gui_stringToWString(placeholder));
        return Value(true);
    }

    Value builtin_vg_create_slider(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 300;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 40;

        auto slider = std::make_unique<gui::Slider>();
        slider->SetBounds(gui::Rect(x, y, x + w, y + h));

        if (args.size() > 4)
        {
            float minVal = gui_valueToFloat(args[4]);
            float maxVal = args.size() > 5 ? gui_valueToFloat(args[5]) : 100;
            slider->SetMinMax(minVal, maxVal);
        }

        gui::Slider *sliderPtr = slider.get();
        window->AddComponent(std::move(slider));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::Slider, sliderPtr);

        return Value(objId);
    }

    Value builtin_vg_slider_set_value(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        float value = gui_valueToFloat(args[1]);

        auto *slider = GUIManager::instance().getTyped<gui::Slider>(objId, GUIObject::Type::Slider);
        if (!slider)
            return Value(false);

        slider->SetValue(value);
        return Value(true);
    }

    Value builtin_vg_slider_get_value(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(0.0);

        std::string objId = args[0].toString();
        auto *slider = GUIManager::instance().getTyped<gui::Slider>(objId, GUIObject::Type::Slider);
        if (!slider)
            return Value(0.0);

        return Value(static_cast<double>(slider->GetValue()));
    }

    Value builtin_vg_slider_on_changed(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        GUIBinding binding;
        if (!gui_parseCallback(args, binding))
        {
            std::cerr << "[Error] Invalid callback function for slider" << std::endl;
            return Value(false);
        }

        auto *slider = GUIManager::instance().getTyped<gui::Slider>(objId, GUIObject::Type::Slider);
        if (!slider)
            return Value(false);

        GUIManager::instance().registerSliderCallback(objId, binding.fn, std::move(binding.bound));
        slider->OnValueChanged([objId](float value)
                               { GUIManager::instance().triggerSliderChange(objId, value); });

        return Value(true);
    }

    Value builtin_vg_create_progressbar(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 300;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 20;

        auto progress = std::make_unique<gui::ProgressBar>();
        progress->SetBounds(gui::Rect(x, y, x + w, y + h));
        progress->SetHeight(h);

        gui::ProgressBar *progressPtr = progress.get();
        window->AddComponent(std::move(progress));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::ProgressBar, progressPtr);

        return Value(objId);
    }

    Value builtin_vg_progressbar_set_value(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        float value = gui_valueToFloat(args[1]);

        auto *progress = GUIManager::instance().getTyped<gui::ProgressBar>(objId, GUIObject::Type::ProgressBar);
        if (!progress)
            return Value(false);

        progress->SetTargetValue(value, 0.3f);
        return Value(true);
    }

    Value builtin_vg_progressbar_get_value(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(0.0);

        std::string objId = args[0].toString();
        auto *progress = GUIManager::instance().getTyped<gui::ProgressBar>(objId, GUIObject::Type::ProgressBar);
        if (!progress)
            return Value(0.0);

        return Value(static_cast<double>(progress->GetValue()));
    }

    Value builtin_vg_progressbar_set_color(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        gui::Color color = gui_valueToColor(args[1]);

        auto *progress = GUIManager::instance().getTyped<gui::ProgressBar>(objId, GUIObject::Type::ProgressBar);
        if (!progress)
            return Value(false);

        progress->SetProgressColor(color);
        return Value(true);
    }

    Value builtin_vg_create_card(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 250;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 200;
        std::string title = args.size() > 4 ? args[4].toString() : "Card Title";
        std::string subtitle = args.size() > 5 ? args[5].toString() : "Card subtitle";

        auto card = std::make_unique<gui::Card>();
        card->SetBounds(gui::Rect(x, y, x + w, y + h));
        card->SetTitle(gui_stringToWString(title));
        card->SetSubtitle(gui_stringToWString(subtitle));

        gui::Card *cardPtr = card.get();
        window->AddComponent(std::move(card));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::Card, cardPtr);

        return Value(objId);
    }

    Value builtin_vg_card_set_title(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        std::string title = args[1].toString();

        auto *card = GUIManager::instance().getTyped<gui::Card>(objId, GUIObject::Type::Card);
        if (!card)
            return Value(false);

        card->SetTitle(gui_stringToWString(title));
        return Value(true);
    }

    Value builtin_vg_card_set_elevation(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        float elevation = gui_valueToFloat(args[1]);

        auto *card = GUIManager::instance().getTyped<gui::Card>(objId, GUIObject::Type::Card);
        if (!card)
            return Value(false);

        card->SetElevation(elevation);
        return Value(true);
    }

    Value builtin_vg_create_combobox(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 200;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 35;

        auto combobox = std::make_unique<gui::ComboBox>();
        combobox->SetBounds(gui::Rect(x, y, x + w, y + h));

        gui::ComboBox *comboboxPtr = combobox.get();
        window->AddComponent(std::move(combobox));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::ComboBox, comboboxPtr);

        return Value(objId);
    }

    Value builtin_vg_combobox_add_item(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        std::string item = args[1].toString();

        auto *combobox = GUIManager::instance().getTyped<gui::ComboBox>(objId, GUIObject::Type::ComboBox);
        if (!combobox)
            return Value(false);

        combobox->AddItem(gui_stringToWString(item));
        return Value(true);
    }

    Value builtin_vg_combobox_clear(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(false);

        std::string objId = args[0].toString();
        auto *combobox = GUIManager::instance().getTyped<gui::ComboBox>(objId, GUIObject::Type::ComboBox);
        if (!combobox)
            return Value(false);

        combobox->ClearItems();
        return Value(true);
    }

    Value builtin_vg_combobox_set_selected(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        int index = gui_valueToInt(args[1]);

        auto *combobox = GUIManager::instance().getTyped<gui::ComboBox>(objId, GUIObject::Type::ComboBox);
        if (!combobox)
            return Value(false);

        combobox->SetSelectedIndex(index);
        return Value(true);
    }

    Value builtin_vg_combobox_get_selected(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(-1);

        std::string objId = args[0].toString();
        auto *combobox = GUIManager::instance().getTyped<gui::ComboBox>(objId, GUIObject::Type::ComboBox);
        if (!combobox)
            return Value(-1);

        return Value(static_cast<double>(combobox->GetSelectedIndex()));
    }

    Value builtin_vg_combobox_on_selected(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        GUIBinding binding;
        if (!gui_parseCallback(args, binding))
        {
            std::cerr << "[Error] Invalid callback function for combobox" << std::endl;
            return Value(false);
        }

        auto *combobox = GUIManager::instance().getTyped<gui::ComboBox>(objId, GUIObject::Type::ComboBox);
        if (!combobox)
            return Value(false);

        GUIManager::instance().registerComboboxCallback(objId, binding.fn, std::move(binding.bound));
        combobox->OnSelectionChanged([objId](int index, const std::wstring &text)
                                     { GUIManager::instance().triggerComboboxSelection(objId, index, text); });

        return Value(true);
    }

    Value builtin_vg_create_listbox(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 200;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 150;

        auto listbox = std::make_unique<gui::ListBox>();
        listbox->SetBounds(gui::Rect(x, y, x + w, y + h));

        gui::ListBox *listboxPtr = listbox.get();
        window->AddComponent(std::move(listbox));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::ListBox, listboxPtr);

        return Value(objId);
    }

    Value builtin_vg_listbox_add_item(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        std::string item = args[1].toString();

        auto *listbox = GUIManager::instance().getTyped<gui::ListBox>(objId, GUIObject::Type::ListBox);
        if (!listbox)
            return Value(false);

        listbox->AddItem(gui_stringToWString(item));
        return Value(true);
    }

    Value builtin_vg_listbox_clear(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(false);

        std::string objId = args[0].toString();
        auto *listbox = GUIManager::instance().getTyped<gui::ListBox>(objId, GUIObject::Type::ListBox);
        if (!listbox)
            return Value(false);

        listbox->ClearItems();
        return Value(true);
    }

    Value builtin_vg_listbox_set_selected(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        int index = gui_valueToInt(args[1]);

        auto *listbox = GUIManager::instance().getTyped<gui::ListBox>(objId, GUIObject::Type::ListBox);
        if (!listbox)
            return Value(false);

        listbox->SetSelectedIndex(index);
        return Value(true);
    }

    Value builtin_vg_listbox_get_selected(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(-1);

        std::string objId = args[0].toString();
        auto *listbox = GUIManager::instance().getTyped<gui::ListBox>(objId, GUIObject::Type::ListBox);
        if (!listbox)
            return Value(-1);

        return Value(static_cast<double>(listbox->GetSelectedIndex()));
    }

    Value builtin_vg_listbox_on_selected(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        GUIBinding binding;
        if (!gui_parseCallback(args, binding))
        {
            std::cerr << "[Error] Invalid callback function for listbox" << std::endl;
            return Value(false);
        }

        auto *listbox = GUIManager::instance().getTyped<gui::ListBox>(objId, GUIObject::Type::ListBox);
        if (!listbox)
            return Value(false);

        GUIManager::instance().registerListboxCallback(objId, binding.fn, std::move(binding.bound));
        listbox->OnSelectionChanged([objId](int index, const std::wstring &text)
                                    { GUIManager::instance().triggerListboxSelection(objId, index, text); });

        return Value(true);
    }

    Value builtin_vg_create_tabcontrol(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 500;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 400;

        auto tabcontrol = std::make_unique<gui::TabControl>();
        tabcontrol->SetBounds(gui::Rect(x, y, x + w, y + h));

        gui::TabControl *tabcontrolPtr = tabcontrol.get();
        window->AddComponent(std::move(tabcontrol));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::TabControl, tabcontrolPtr);

        return Value(objId);
    }

    Value builtin_vg_tabcontrol_add_tab(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        std::string title = args[1].toString();

        auto *tabcontrol = GUIManager::instance().getTyped<gui::TabControl>(objId, GUIObject::Type::TabControl);
        if (!tabcontrol)
            return Value(false);

        tabcontrol->AddTab(gui_stringToWString(title));
        return Value(true);
    }

    Value builtin_vg_tabcontrol_set_selected(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        int index = gui_valueToInt(args[1]);

        auto *tabcontrol = GUIManager::instance().getTyped<gui::TabControl>(objId, GUIObject::Type::TabControl);
        if (!tabcontrol)
            return Value(false);

        tabcontrol->SetSelectedIndex(index);
        return Value(true);
    }

    Value builtin_vg_create_spinner(const std::vector<Value> &args)
    {
        auto *window = GUIManager::instance().getMainWindow();
        if (!window)
            return Value(nullptr);

        float x = args.size() > 0 ? gui_valueToFloat(args[0]) : 50;
        float y = args.size() > 1 ? gui_valueToFloat(args[1]) : 50;
        float w = args.size() > 2 ? gui_valueToFloat(args[2]) : 50;
        float h = args.size() > 3 ? gui_valueToFloat(args[3]) : 50;

        auto spinner = std::make_unique<gui::Spinner>();
        spinner->SetBounds(gui::Rect(x, y, x + w, y + h));
        spinner->Start();

        gui::Spinner *spinnerPtr = spinner.get();
        window->AddComponent(std::move(spinner));

        std::string objId = GUIManager::instance().storeObject(
            GUIObject::Type::Spinner, spinnerPtr);

        return Value(objId);
    }

    Value builtin_vg_spinner_start(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(false);

        std::string objId = args[0].toString();
        auto *spinner = GUIManager::instance().getTyped<gui::Spinner>(objId, GUIObject::Type::Spinner);
        if (!spinner)
            return Value(false);

        spinner->Start();
        return Value(true);
    }

    Value builtin_vg_spinner_stop(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(false);

        std::string objId = args[0].toString();
        auto *spinner = GUIManager::instance().getTyped<gui::Spinner>(objId, GUIObject::Type::Spinner);
        if (!spinner)
            return Value(false);

        spinner->Stop();
        return Value(true);
    }

    Value builtin_vg_set_position(const std::vector<Value> &args)
    {
        if (args.size() < 3)
            return Value(false);

        std::string objId = args[0].toString();
        float x = gui_valueToFloat(args[1]);
        float y = gui_valueToFloat(args[2]);

        auto *comp = GUIManager::instance().getUIComponent(objId);
        if (!comp)
            return Value(false);
        gui::Rect bounds = comp->GetBounds();
        float w = bounds.Width();
        float h = bounds.Height();
        comp->SetBounds(gui::Rect(x, y, x + w, y + h));

        return Value(true);
    }

    Value builtin_vg_set_size(const std::vector<Value> &args)
    {
        if (args.size() < 3)
            return Value(false);

        std::string objId = args[0].toString();
        float w = gui_valueToFloat(args[1]);
        float h = gui_valueToFloat(args[2]);

        auto *comp = GUIManager::instance().getUIComponent(objId);
        if (!comp)
            return Value(false);
        gui::Rect bounds = comp->GetBounds();
        comp->SetBounds(gui::Rect(bounds.left, bounds.top, bounds.left + w, bounds.top + h));

        return Value(true);
    }

    Value builtin_vg_set_visible(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        bool visible = gui_valueToBool(args[1]);

        auto *comp = GUIManager::instance().getUIComponent(objId);
        if (!comp)
            return Value(false);
        comp->SetVisible(visible);

        return Value(true);
    }

    Value builtin_vg_set_enabled(const std::vector<Value> &args)
    {
        if (args.size() < 2)
            return Value(false);

        std::string objId = args[0].toString();
        bool enabled = gui_valueToBool(args[1]);

        auto *comp = GUIManager::instance().getUIComponent(objId);
        if (!comp)
            return Value(false);
        comp->SetEnabled(enabled);

        return Value(true);
    }

    Value builtin_vg_destroy(const std::vector<Value> &args)
    {
        if (args.empty())
            return Value(false);

        std::string objId = args[0].toString();

        auto *window = GUIManager::instance().getMainWindow();
        if (window)
        {
            auto *obj = GUIManager::instance().getObject(objId);
            if (obj && obj->ptr)
            {
            }
        }

        GUIManager::instance().removeObject(objId);
        return Value(true);
    }
}