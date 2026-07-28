using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace LlmFoundationInstaller
{
    internal static class OperatorGuideDashboard
    {
        private sealed class GuidePage
        {
            public string label { get; set; }
            public string eyebrow { get; set; }
            public string title { get; set; }
            public string summary { get; set; }
            public string[] steps { get; set; }
            public string note { get; set; }
        }

        public static void Bind(
            UserControl host,
            string bundleRoot,
            bool interactive
        )
        {
            Button open = host.FindName(
                "OpenGuideDashboard"
            ) as Button;
            if (open == null)
            {
                return;
            }
            open.ToolTip =
                "Открыть встроенную интерактивную инструкцию";
            if (!interactive)
            {
                return;
            }
            open.Click += delegate
            {
                EditionProfile edition =
                    EditionProfile.LoadEmbedded();
                Window dashboard = new Window
                {
                    Title = "K-7 · Интерактивная инструкция · " +
                        EditionLabel(edition),
                    Width = 1180,
                    Height = 760,
                    MinWidth = 980,
                    MinHeight = 680,
                    WindowStartupLocation =
                        WindowStartupLocation.CenterOwner,
                    Background =
                        EditionTheme.WindowBackground(edition)
                };
                dashboard.Content = Create(
                    bundleRoot,
                    delegate(string targetId)
                    {
                        ApplyHostSelection(host, targetId);
                        dashboard.Close();
                    }
                );
                Window owner = Window.GetWindow(host);
                if (owner != null)
                {
                    dashboard.Owner = owner;
                }
                dashboard.ShowDialog();
            };
        }

        public static UserControl Create(string bundleRoot)
        {
            return Create(bundleRoot, null);
        }

        private static UserControl Create(
            string bundleRoot,
            Action<string> selectTarget
        )
        {
            EditionProfile edition = EditionProfile.LoadEmbedded();
            bool owner = edition.owner_controlled;
            Color background = owner
                ? Color.FromRgb(7, 30, 34)
                : Color.FromRgb(246, 247, 245);
            Color panel = owner
                ? Color.FromRgb(12, 41, 46)
                : Colors.White;
            Color panelRaised = owner
                ? Color.FromRgb(16, 52, 58)
                : Color.FromRgb(255, 255, 255);
            Color line = owner
                ? Color.FromRgb(40, 80, 87)
                : Color.FromRgb(215, 223, 222);
            Color text = owner
                ? Color.FromRgb(234, 243, 242)
                : Color.FromRgb(7, 30, 34);
            Color muted = owner
                ? Color.FromRgb(145, 170, 173)
                : Color.FromRgb(98, 114, 117);
            Color accent = owner
                ? Color.FromRgb(48, 188, 237)
                : Color.FromRgb(252, 73, 18);
            Color secondary = Color.FromRgb(119, 203, 185);

            GuidePage[] pages = Pages(edition);
            UserControl result = new UserControl
            {
                FontFamily = new FontFamily("Segoe UI"),
                Background = Brush(background),
                SnapsToDevicePixels = true,
                UseLayoutRounding = true
            };
            Grid root = new Grid
            {
                Margin = new Thickness(24)
            };
            root.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = new GridLength(260)
            });
            root.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = new GridLength(20)
            });
            root.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = new GridLength(1, GridUnitType.Star)
            });

            Border rail = Card(panel, line, 0, owner ? 8 : 16);
            Grid.SetColumn(rail, 0);
            Grid railLayout = new Grid
            {
                Margin = new Thickness(22)
            };
            railLayout.RowDefinitions.Add(new RowDefinition
            {
                Height = GridLength.Auto
            });
            railLayout.RowDefinitions.Add(new RowDefinition
            {
                Height = GridLength.Auto
            });
            railLayout.RowDefinitions.Add(new RowDefinition
            {
                Height = new GridLength(1, GridUnitType.Star)
            });
            railLayout.RowDefinitions.Add(new RowDefinition
            {
                Height = GridLength.Auto
            });
            rail.Child = railLayout;

            StackPanel identity = new StackPanel();
            Border logo = new Border
            {
                Width = 54,
                Height = 54,
                HorizontalAlignment = HorizontalAlignment.Left,
                Background = Brush(owner ? accent : Color.FromRgb(7, 30, 34)),
                CornerRadius = new CornerRadius(owner ? 7 : 27),
                Child = Label(
                    "K7",
                    owner ? Color.FromRgb(7, 30, 34) : Colors.White,
                    22,
                    FontWeights.Bold,
                    "Bahnschrift SemiCondensed"
                )
            };
            identity.Children.Add(logo);
            identity.Children.Add(Label(
                owner ? "КОНТУР ВЛАДЕЛЬЦА" : "РАБОЧАЯ СРЕДА ИИ",
                owner ? accent : Color.FromRgb(20, 107, 90),
                10,
                FontWeights.Bold,
                "Cascadia Mono",
                new Thickness(0, 17, 0, 0)
            ));
            identity.Children.Add(Label(
                "Интерактивная\nинструкция",
                text,
                25,
                FontWeights.SemiBold,
                "Bahnschrift SemiCondensed",
                new Thickness(0, 6, 0, 0)
            ));
            identity.Children.Add(Label(
                EditionLabel(edition) + " · " +
                    ProductLabel(edition),
                muted,
                10,
                FontWeights.Normal,
                "Cascadia Mono",
                new Thickness(0, 7, 0, 0)
            ));
            railLayout.Children.Add(identity);

            Border divider = new Border
            {
                BorderBrush = Brush(line),
                BorderThickness = new Thickness(0, 1, 0, 0),
                Margin = new Thickness(0, 20, 0, 15)
            };
            Grid.SetRow(divider, 1);
            railLayout.Children.Add(divider);

            StackPanel navigation = new StackPanel();
            Grid.SetRow(navigation, 2);
            railLayout.Children.Add(navigation);

            StackPanel railFooter = new StackPanel();
            TextBlock boundary = Label(
                owner
                    ? "ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА\nРАСПРОСТРАНЕНИЕ ЗАПРЕЩЕНО"
                    : "K-7 · ДЛЯ СОТРУДНИКОВ\nЛОКАЛЬНАЯ РАБОТА",
                owner ? Color.FromRgb(252, 122, 77) : secondary,
                10,
                FontWeights.SemiBold,
                "Cascadia Mono"
            );
            boundary.LineHeight = 18;
            railFooter.Children.Add(boundary);
            Button closeGuide = new Button
            {
                Content = "Вернуться к выбору",
                Height = 38,
                Margin = new Thickness(0, 13, 0, 0),
                Background = Brush(owner
                    ? Color.FromRgb(16, 52, 58)
                    : Color.FromRgb(255, 240, 233)),
                Foreground = Brush(owner
                    ? accent
                    : Color.FromRgb(134, 50, 20)),
                BorderBrush = Brush(accent),
                BorderThickness = new Thickness(1),
                Cursor = System.Windows.Input.Cursors.Hand
            };
            closeGuide.Click += delegate
            {
                Window window = Window.GetWindow(result);
                if (window != null)
                {
                    window.Close();
                }
            };
            railFooter.Children.Add(closeGuide);
            Grid.SetRow(railFooter, 3);
            railLayout.Children.Add(railFooter);

            Grid content = new Grid();
            Grid.SetColumn(content, 2);
            content.RowDefinitions.Add(new RowDefinition
            {
                Height = GridLength.Auto
            });
            content.RowDefinitions.Add(new RowDefinition
            {
                Height = new GridLength(1, GridUnitType.Star)
            });
            content.RowDefinitions.Add(new RowDefinition
            {
                Height = GridLength.Auto
            });
            StackPanel heading = new StackPanel();
            TextBlock eyebrow = Label(
                "",
                owner ? accent : Color.FromRgb(20, 107, 90),
                10,
                FontWeights.Bold,
                "Cascadia Mono"
            );
            TextBlock title = Label(
                "",
                text,
                31,
                FontWeights.SemiBold,
                "Bahnschrift SemiCondensed",
                new Thickness(0, 5, 0, 0)
            );
            TextBlock summary = Label(
                "",
                muted,
                13,
                FontWeights.Normal,
                "Segoe UI",
                new Thickness(0, 6, 0, 0)
            );
            summary.TextWrapping = TextWrapping.Wrap;
            heading.Children.Add(eyebrow);
            heading.Children.Add(title);
            heading.Children.Add(summary);
            content.Children.Add(heading);

            Border pageCard = Card(panelRaised, line, 22, owner ? 8 : 16);
            pageCard.Margin = new Thickness(0, 22, 0, 18);
            Grid.SetRow(pageCard, 1);
            StackPanel pageBody = new StackPanel();
            pageCard.Child = pageBody;
            content.Children.Add(pageCard);

            Grid facts = new Grid();
            facts.ColumnDefinitions.Add(new ColumnDefinition());
            facts.ColumnDefinitions.Add(new ColumnDefinition());
            facts.ColumnDefinitions.Add(new ColumnDefinition());
            facts.Children.Add(Fact(
                "ВЕРСИЯ",
                EditionLabel(edition).ToUpperInvariant(),
                panel,
                line,
                text,
                muted,
                accent,
                0
            ));
            facts.Children.Add(Fact(
                "КЛИЕНТЫ",
                edition.owner_controlled
                    ? "3 / ВЛАДЕЛЕЦ"
                    : "2 / СОТРУДНИК",
                panel,
                line,
                text,
                muted,
                secondary,
                1
            ));
            facts.Children.Add(Fact(
                "КОМПЛЕКТ",
                BundleState(bundleRoot),
                panel,
                line,
                text,
                muted,
                accent,
                2
            ));
            Grid.SetRow(facts, 2);
            content.Children.Add(facts);

            List<Button> buttons = new List<Button>();
            Action<int> activate = delegate(int index)
            {
                GuidePage page = pages[index];
                eyebrow.Text = page.eyebrow;
                title.Text = page.title;
                summary.Text = page.summary;
                pageBody.Children.Clear();
                pageBody.Children.Add(Label(
                    "ПОРЯДОК ДЕЙСТВИЙ",
                    owner ? accent : Color.FromRgb(20, 107, 90),
                    10,
                    FontWeights.Bold,
                    "Cascadia Mono"
                ));
                for (int step = 0; step < page.steps.Length; step++)
                {
                    pageBody.Children.Add(Step(
                        step + 1,
                        page.steps[step],
                        panel,
                        line,
                        text,
                        muted,
                        accent,
                        owner
                    ));
                }
                if (index == 0)
                {
                    pageBody.Children.Add(TargetChooser(
                        edition,
                        selectTarget,
                        panel,
                        line,
                        text,
                        accent,
                        secondary,
                        owner
                    ));
                }
                Border note = new Border
                {
                    Background = Brush(owner
                        ? Color.FromRgb(64, 37, 31)
                        : Color.FromRgb(255, 240, 233)),
                    BorderBrush = Brush(owner
                        ? Color.FromRgb(120, 67, 50)
                        : Color.FromRgb(252, 157, 123)),
                    BorderThickness = new Thickness(1),
                    CornerRadius = new CornerRadius(owner ? 5 : 10),
                    Padding = new Thickness(14, 11, 14, 11),
                    Margin = new Thickness(0, 15, 0, 0),
                    Child = Label(
                        page.note,
                        owner
                            ? Color.FromRgb(252, 182, 156)
                            : Color.FromRgb(134, 50, 20),
                        11,
                        FontWeights.SemiBold,
                        "Segoe UI"
                    )
                };
                pageBody.Children.Add(note);
                for (int buttonIndex = 0;
                    buttonIndex < buttons.Count;
                    buttonIndex++)
                {
                    bool active = buttonIndex == index;
                    buttons[buttonIndex].Background = Brush(
                        active
                            ? (owner
                                ? Color.FromRgb(16, 52, 58)
                                : Color.FromRgb(255, 240, 233))
                            : Colors.Transparent
                    );
                    buttons[buttonIndex].Foreground = Brush(
                        active ? accent : muted
                    );
                    buttons[buttonIndex].BorderBrush = Brush(
                        active ? accent : Colors.Transparent
                    );
                }
            };

            for (int index = 0; index < pages.Length; index++)
            {
                int pageIndex = index;
                Button button = new Button
                {
                    Content = pages[index].label,
                    Height = 46,
                    HorizontalContentAlignment =
                        HorizontalAlignment.Left,
                    Padding = new Thickness(13, 0, 13, 0),
                    Margin = new Thickness(0, 0, 0, 7),
                    Background = Brushes.Transparent,
                    Foreground = Brush(muted),
                    BorderThickness = new Thickness(1),
                    BorderBrush = Brushes.Transparent,
                    FontFamily = new FontFamily("Cascadia Mono"),
                    FontSize = 10,
                    FontWeight = FontWeights.SemiBold,
                    Cursor = System.Windows.Input.Cursors.Hand
                };
                button.Click += delegate
                {
                    activate(pageIndex);
                };
                buttons.Add(button);
                navigation.Children.Add(button);
            }

            root.Children.Add(rail);
            root.Children.Add(content);
            result.Content = root;
            activate(0);
            return result;
        }

        private static GuidePage[] Pages(EditionProfile edition)
        {
            bool owner = edition.owner_controlled;
            string product = edition.product_role == "Installer"
                ? "установщик"
                : "центр запуска";
            return new[]
            {
                new GuidePage
                {
                    label = "01 / СТАРТ",
                    eyebrow = "БЫСТРЫЙ СТАРТ / " +
                        EditionLabel(edition).ToUpperInvariant(),
                    title = product == "установщик"
                        ? "Установить среду без лишних шагов"
                        : "Запустить нужный клиент и маршрут",
                    summary = owner
                        ? "Codex + Claude + OpenCode. Claude остаётся кандидатом только для владельца, пока маркер провайдера не подтверждён."
                        : "Codex + OpenCode. Два клиента, один понятный рабочий контур.",
                    steps = product == "установщик"
                        ? new[]
                        {
                            "Проверьте найденные клиенты и оставьте выбранными нужные базы.",
                            "Выберите прямое подключение, системный VPN или профиль SingBox.",
                            "Запустите установку. Вход выполняйте уже внутри официального клиента."
                        }
                        : new[]
                        {
                            "Выберите Codex или OpenCode" +
                                (owner ? "; Claude доступен только владельцу." : "."),
                            "Укажите прямое подключение, VPN, SingBox HTTP или SingBox HTTPS.",
                            "Нажмите «Запустить». Центр запускает только проверенную точную цель."
                        },
                    note = "Никаких вызовов модели, входов в аккаунт или сетевых загрузок без отдельного действия пользователя."
                },
                new GuidePage
                {
                    label = "02 / МАРШРУТЫ",
                    eyebrow = "МАРШРУТИЗАЦИЯ / ТОЛЬКО ПРОЦЕСС",
                    title = "Маршрут меняется только для запуска",
                    summary = "Профиль соединения не выдаёт право использования сервиса и не меняет правила провайдера.",
                    steps = new[]
                    {
                        "Прямое подключение очищает унаследованные прокси-переменные дочернего процесса.",
                        "VPN использует уже активную системную VPN-маршрутизацию.",
                        "SingBox HTTP/HTTPS поднимает локальный шлюз и передаёт его только запущенному процессу."
                    },
                    note = "Маршруты не предназначены для обхода региона, блокировки аккаунта или защитных ограничений провайдера."
                },
                new GuidePage
                {
                    label = "03 / БЕЗОПАСНОСТЬ",
                    eyebrow = "ГРАНИЦА ДОВЕРИЯ / ЛОКАЛЬНО",
                    title = "Проверяем байты, не собираем секреты",
                    summary = owner
                        ? "Версия владельца: распространение запрещено (distribution_allowed=false)."
                        : "Версия для сотрудников содержит только принятые пакеты Codex и OpenCode.",
                    steps = new[]
                    {
                        "Каждый пакет и среда выполнения сверяются со встроенным манифестом и SHA-256.",
                        "Авторизация, OAuth, cookies и API-ключи остаются внутри официальных клиентов.",
                        owner
                            ? "Наличие пакета Claude не означает допуск провайдера; состояние отображается отдельно."
                            : "Установщик не содержит Claude и не предлагает сотруднику общий аккаунт."
                    },
                    note = owner
                        ? "ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА · распространение запрещено (distribution_allowed=false)"
                        : "Отдельный аккаунт каждого сотрудника; общие учётные данные запрещены."
                },
                new GuidePage
                {
                    label = "04 / ВОССТАНОВЛЕНИЕ",
                    eyebrow = "ВОССТАНОВЛЕНИЕ / ПРОВЕРКА",
                    title = "Восстановление сохраняет пользовательские данные",
                    summary = "Отчёты и резервные копии лежат локально в профиле пользователя.",
                    steps = new[]
                    {
                        "Откройте %USERPROFILE%\\.llm-foundation\\reports\\ и найдите последний отчёт.",
                        "Запустите диагностику нужной базы; при ошибке используйте восстановление.",
                        "Восстановление возвращает управляемую поверхность, не удаляя проекты, историю и авторизацию."
                    },
                    note = "При нарушении SHA-256 или структуры файлов запуск блокируется; повреждённая среда выполнения автоматически не перезаписывается."
                }
            };
        }

        private static string BundleState(string bundleRoot)
        {
            string manifest = Path.Combine(
                Path.GetFullPath(bundleRoot),
                "bundle-manifest.json"
            );
            return File.Exists(manifest)
                ? "МАНИФЕСТ ГОТОВ"
                : "ВСТРОЕНО";
        }

        internal static bool ApplyHostSelection(
            UserControl host,
            string targetId
        )
        {
            ListBox targetList = host.FindName(
                "LaunchTargetList"
            ) as ListBox;
            if (targetList != null)
            {
                return LaunchCenterActions.SelectTarget(
                    host,
                    targetId
                );
            }
            string prefix = targetId.StartsWith(
                    "codex",
                    StringComparison.Ordinal
                )
                ? "Codex"
                : (targetId.StartsWith(
                        "claude",
                        StringComparison.Ordinal
                    )
                    ? "Claude"
                    : (targetId.StartsWith(
                            "opencode",
                            StringComparison.Ordinal
                        )
                        ? "OpenCode"
                        : null));
            CheckBox selected = String.IsNullOrEmpty(prefix)
                ? null
                : host.FindName(prefix + "Selected") as CheckBox;
            if (selected == null || !selected.IsEnabled)
            {
                return false;
            }
            selected.IsChecked = true;
            return true;
        }

        private static Border TargetChooser(
            EditionProfile edition,
            Action<string> selectTarget,
            Color panel,
            Color line,
            Color text,
            Color accent,
            Color secondary,
            bool owner
        )
        {
            Border chooser = Card(panel, line, 14, owner ? 6 : 11);
            chooser.Margin = new Thickness(0, 15, 0, 0);
            StackPanel body = new StackPanel();
            body.Children.Add(Label(
                "ВЫБРАТЬ КЛИЕНТ В ГЛАВНОМ ОКНЕ",
                owner ? accent : Color.FromRgb(20, 107, 90),
                10,
                FontWeights.Bold,
                "Cascadia Mono"
            ));
            WrapPanel actions = new WrapPanel
            {
                Margin = new Thickness(0, 10, 0, 0)
            };
            AddTargetButton(
                actions,
                "Выбрать Codex",
                "codex-desktop",
                selectTarget,
                text,
                accent,
                owner
            );
            if (edition.owner_controlled)
            {
                AddTargetButton(
                    actions,
                    "Выбрать Claude",
                    "claude-code",
                    selectTarget,
                    text,
                    Color.FromRgb(252, 122, 77),
                    owner
                );
            }
            AddTargetButton(
                actions,
                "Выбрать OpenCode",
                "opencode-cli",
                selectTarget,
                text,
                secondary,
                owner
            );
            body.Children.Add(actions);
            chooser.Child = body;
            return chooser;
        }

        private static void AddTargetButton(
            Panel actions,
            string label,
            string targetId,
            Action<string> selectTarget,
            Color text,
            Color accent,
            bool owner
        )
        {
            Button button = new Button
            {
                Content = label,
                Height = 38,
                Padding = new Thickness(13, 0, 13, 0),
                Margin = new Thickness(0, 0, 9, 0),
                Background = Brush(owner
                    ? Color.FromRgb(16, 52, 58)
                    : Colors.White),
                Foreground = Brush(text),
                BorderBrush = Brush(accent),
                BorderThickness = new Thickness(1),
                Cursor = System.Windows.Input.Cursors.Hand
            };
            button.Click += delegate
            {
                if (selectTarget != null)
                {
                    selectTarget(targetId);
                }
                else
                {
                    Window window = Window.GetWindow(button);
                    if (window != null)
                    {
                        window.Close();
                    }
                }
            };
            actions.Children.Add(button);
        }

        private static string EditionLabel(EditionProfile edition)
        {
            return edition.owner_controlled
                ? "Владелец"
                : "Для сотрудников";
        }

        private static string ProductLabel(EditionProfile edition)
        {
            return edition.product_role == "Installer"
                ? "УСТАНОВЩИК"
                : "ЦЕНТР ЗАПУСКА";
        }

        private static Border Fact(
            string caption,
            string value,
            Color panel,
            Color line,
            Color text,
            Color muted,
            Color accent,
            int column
        )
        {
            Border card = Card(panel, line, 14, 8);
            card.Margin = new Thickness(
                column == 0 ? 0 : 7,
                0,
                column == 2 ? 0 : 7,
                0
            );
            StackPanel body = new StackPanel();
            body.Children.Add(Label(
                caption,
                muted,
                9,
                FontWeights.SemiBold,
                "Cascadia Mono"
            ));
            body.Children.Add(Label(
                value,
                accent,
                12,
                FontWeights.Bold,
                "Cascadia Mono",
                new Thickness(0, 6, 0, 0)
            ));
            card.Child = body;
            Grid.SetColumn(card, column);
            return card;
        }

        private static Border Step(
            int number,
            string value,
            Color panel,
            Color line,
            Color text,
            Color muted,
            Color accent,
            bool owner
        )
        {
            Border row = new Border
            {
                BorderBrush = Brush(line),
                BorderThickness = new Thickness(0, 0, 0, 1),
                Padding = new Thickness(0, 13, 0, 13)
            };
            Grid layout = new Grid();
            layout.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = new GridLength(42)
            });
            layout.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = new GridLength(1, GridUnitType.Star)
            });
            Border badge = new Border
            {
                Width = 28,
                Height = 28,
                HorizontalAlignment = HorizontalAlignment.Left,
                Background = Brush(owner
                    ? Color.FromRgb(16, 52, 58)
                    : Color.FromRgb(255, 240, 233)),
                BorderBrush = Brush(accent),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(owner ? 4 : 14),
                Child = Label(
                    number.ToString("00"),
                    accent,
                    10,
                    FontWeights.Bold,
                    "Cascadia Mono"
                )
            };
            TextBlock copy = Label(
                value,
                text,
                13,
                FontWeights.Normal,
                "Segoe UI"
            );
            copy.TextWrapping = TextWrapping.Wrap;
            copy.VerticalAlignment = VerticalAlignment.Center;
            Grid.SetColumn(copy, 1);
            layout.Children.Add(badge);
            layout.Children.Add(copy);
            row.Child = layout;
            return row;
        }

        private static Border Card(
            Color background,
            Color line,
            double padding,
            double radius
        )
        {
            return new Border
            {
                Background = Brush(background),
                BorderBrush = Brush(line),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(radius),
                Padding = new Thickness(padding)
            };
        }

        private static TextBlock Label(
            string value,
            Color color,
            double size,
            FontWeight weight,
            string family,
            Thickness margin = default(Thickness)
        )
        {
            return new TextBlock
            {
                Text = value,
                Foreground = Brush(color),
                FontSize = size,
                FontWeight = weight,
                FontFamily = new FontFamily(family),
                Margin = margin,
                VerticalAlignment = VerticalAlignment.Center
            };
        }

        private static SolidColorBrush Brush(Color value)
        {
            return new SolidColorBrush(value);
        }
    }
}
