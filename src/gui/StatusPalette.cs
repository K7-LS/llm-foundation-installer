using System.Windows.Media;

namespace LlmFoundationInstaller
{
    // Единая карта «тон → цвет» светлых видов (кандидат №4 этапа 2а).
    // Тройка ok/warn/info и мягкие фоны заводились заново в ConnectionUi,
    // InstallerActions и InstallerView — расхождение оттенков между ними
    // ловилось только глазом.
    //
    // Тёмная палитра Launch Center (LaunchCenterActions) и оформление
    // дашборда инструкции (OperatorGuideDashboard) сюда НЕ входят: это
    // отдельные темы, а не статусные тона.
    internal static class StatusPalette
    {
        public static readonly Color Ok = Color.FromRgb(22, 122, 88);
        public static readonly Color Warn = Color.FromRgb(161, 92, 0);
        public static readonly Color Info = Color.FromRgb(49, 87, 199);

        public static readonly Color OkSurface = Color.FromRgb(231, 246, 240);
        public static readonly Color WarnSurface = Color.FromRgb(255, 244, 222);
        public static readonly Color InfoSurface = Color.FromRgb(234, 240, 255);

        public static readonly Color ActiveStep = Color.FromRgb(65, 105, 225);
        public static readonly Color IdleStepBorder = Color.FromRgb(82, 96, 120);

        public static Color Tone(string tone)
        {
            if (tone == ConnectionStatusModel.ToneOk)
            {
                return Ok;
            }
            if (tone == ConnectionStatusModel.ToneWarn)
            {
                return Warn;
            }
            return Info;
        }

        public static SolidColorBrush Brush(Color color)
        {
            return new SolidColorBrush(color);
        }
    }
}
