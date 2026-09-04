using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace LlmFoundationInstaller
{
    internal static class InstallerView
    {
        public static UserControl Create(
            string bundleRoot,
            bool loadConnectionState = true
        )
        {
            return Create(
                bundleRoot,
                EditionProfile.LoadEmbedded(),
                loadConnectionState
            );
        }

        // Роль берёт профиль вызывающего: --launch-center-ui переключает
        // её в профиле, загруженном в Main. Повторная загрузка встроенного
        // профиля возвращала Installer, и окно центра запуска получало вид
        // установщика под заголовком «Launch Center» (canary 0.4.4).
        public static UserControl Create(
            string bundleRoot,
            EditionProfile edition,
            bool loadConnectionState
        )
        {
            string viewResource = EditionTheme.ViewResource(edition);
            UserControl view;
            try
            {
                view = (UserControl)Application.LoadComponent(
                    new Uri("/" + viewResource, UriKind.Relative)
                );
            }
            catch (IOException exception)
            {
                throw new InvalidOperationException(
                    "Product UI resource is missing: " + viewResource,
                    exception
                );
            }
            {
                if (edition.product_role == "Installer")
                {
                    ApplyCatalog(
                        view,
                        ProductCatalog.Inspect(
                            bundleRoot,
                            loadConnectionState
                        )
                    );
                }
                ConnectionUi.Bind(
                    view,
                    bundleRoot,
                    loadConnectionState
                );
                if (edition.product_role == "Installer")
                {
                    InstallerActions.Bind(
                        view,
                        bundleRoot,
                        loadConnectionState
                    );
                }
                else
                {
                    LaunchCenterActions.Bind(
                        view,
                        bundleRoot,
                        loadConnectionState
                    );
                }
                OperatorGuideDashboard.Bind(
                    view,
                    bundleRoot,
                    loadConnectionState
                );
                ChromeProxyLauncher.Bind(view);
                view.Tag = viewResource;
                return view;
            }
        }

        internal static void ApplyCatalog(
            UserControl view,
            CatalogResult catalog
        )
        {
            foreach (TargetRow row in catalog.targets)
            {
                string prefix = row.id == "codex"
                    ? "Codex"
                    : (row.id == "claude" ? "Claude" : "OpenCode");
                TextBlock status = view.FindName(prefix + "Status") as TextBlock;
                Border badge = view.FindName(
                    prefix + "StatusBadge"
                ) as Border;
                CheckBox selected = view.FindName(prefix + "Selected") as CheckBox;
                if (status != null)
                {
                    status.Text = row.package_state == "accepted"
                        ? (row.client_state == "missing"
                            ? "Клиент не найден · требуется " +
                                row.supported_version
                            : (row.client_state == "unsupported"
                                ? "Версия " + row.detected_version +
                                    " · требуется " + row.supported_version
                                : (row.client_state == "ready"
                                    ? "Готово · клиент " +
                                        row.detected_version
                                    : "Пакет проверен · " +
                                        row.supported_version)))
                        : (row.package_state == "tampered"
                            ? "Пакет повреждён · установка запрещена"
                            : (row.client_state == "present_unbound"
                                    ? "Клиент " + row.detected_version +
                                        " найден · пакет не включён"
                                    : (row.client_state == "missing"
                                        ? "Пакет не включён · клиент не найден"
                                        : "Пакет не включён в эту сборку")));
                    bool ready = row.package_state == "accepted" &&
                        (row.client_state == "ready" ||
                            row.client_state == "not_checked");
                    status.Foreground = new SolidColorBrush(
                        ready
                            ? StatusPalette.Ok
                            : StatusPalette.Warn
                    );
                    status.ToolTip = row.package_state == "accepted"
                        ? (row.id == "codex" &&
                            !String.IsNullOrWhiteSpace(row.detected_version)
                            ? "Пакет проверен. Обнаруженная версия клиента: " +
                                row.detected_version
                            : "Пакет проверен. Поддерживаемая версия клиента: " +
                                row.supported_version)
                        : null;
                    if (badge != null)
                    {
                        badge.Background = new SolidColorBrush(
                            ready
                                ? StatusPalette.OkSurface
                                : StatusPalette.WarnSurface
                        );
                    }
                }
                if (selected != null)
                {
                    bool eligible = row.package_state == "accepted";
                    selected.IsEnabled = eligible;
                    selected.IsChecked = eligible;
                }
            }

            Button action = view.FindName("PrimaryAction") as Button;
            TextBlock statusText = view.FindName("StatusText") as TextBlock;
            Border statusBanner = view.FindName(
                "StatusBanner"
            ) as Border;
            if (action != null)
            {
                action.IsEnabled = catalog.install_enabled;
            }
            if (statusText != null)
            {
                statusText.Text = catalog.install_enabled
                    ? "Компоненты готовы. Следующий шаг — проверяемый план изменений."
                    : (catalog.provider_eligibility == "INVALID_OR_EXPIRED"
                        ? "Нет принятых пакетов клиентов; допуск провайдера истёк или недействителен и перепроверяется отдельно."
                        : "Установка заблокирована: нет принятых пакетов клиентов.");
                statusText.Foreground = new SolidColorBrush(
                    catalog.install_enabled
                        ? StatusPalette.Ok
                        : StatusPalette.Warn
                );
            }
            if (statusBanner != null)
            {
                statusBanner.Background = new SolidColorBrush(
                    catalog.install_enabled
                        ? StatusPalette.OkSurface
                        : StatusPalette.WarnSurface
                );
            }
            SetWorkflowStep(view, 1, false);
        }

        internal static void SetWorkflowStep(
            UserControl view,
            int activeStep,
            bool completedAll
        )
        {
            for (int index = 1; index <= 7; index++)
            {
                Border badge = view.FindName(
                    "Step" + index + "Badge"
                ) as Border;
                TextBlock number = view.FindName(
                    "Step" + index + "Number"
                ) as TextBlock;
                TextBlock title = view.FindName(
                    "Step" + index + "Title"
                ) as TextBlock;
                bool complete = completedAll || index < activeStep;
                bool active = !completedAll && index == activeStep;
                if (badge != null)
                {
                    badge.Background = new SolidColorBrush(
                        complete
                            ? StatusPalette.Ok
                            : (active
                                ? StatusPalette.ActiveStep
                                : Colors.Transparent)
                    );
                    badge.BorderBrush = new SolidColorBrush(
                        complete
                            ? StatusPalette.Ok
                            : (active
                                ? StatusPalette.ActiveStep
                                : StatusPalette.IdleStepBorder)
                    );
                    badge.BorderThickness = complete || active
                        ? new Thickness(0)
                        : new Thickness(1.5);
                }
                if (number != null)
                {
                    number.Text = complete ? "\u2713" : index.ToString(
                        CultureInfo.InvariantCulture
                    );
                    number.Foreground = new SolidColorBrush(
                        complete || active
                            ? Colors.White
                            : Color.FromRgb(170, 182, 200)
                    );
                }
                if (title != null)
                {
                    title.Foreground = new SolidColorBrush(
                        complete || active
                            ? Colors.White
                            : Color.FromRgb(201, 210, 223)
                    );
                }
            }
        }

        public static void RenderPreview(
            UserControl view,
            string outputPath,
            int width,
            int height
        )
        {
            view.Width = width;
            view.Height = height;
            view.Measure(new Size(width, height));
            view.Arrange(new Rect(0, 0, width, height));
            view.UpdateLayout();

            RenderTargetBitmap bitmap = new RenderTargetBitmap(
                width,
                height,
                96,
                96,
                PixelFormats.Pbgra32
            );
            bitmap.Render(view);
            PngBitmapEncoder encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(bitmap));

            string fullPath = Path.GetFullPath(outputPath);
            string parent = Path.GetDirectoryName(fullPath);
            if (!String.IsNullOrEmpty(parent))
            {
                Directory.CreateDirectory(parent);
            }
            using (FileStream stream = File.Create(fullPath))
            {
                encoder.Save(stream);
            }
        }
    }
}
