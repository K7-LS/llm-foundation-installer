using System;
using System.Windows.Media;

namespace LlmFoundationInstaller
{
    internal static class EditionTheme
    {
        public static string ViewResource(EditionProfile edition)
        {
            if (edition == null)
            {
                throw new ArgumentNullException("edition");
            }
            return edition.product_role + edition.edition_id + "View.xaml";
        }

        public static string WindowTitle(EditionProfile edition)
        {
            string product = edition.product_role == "LaunchCenter"
                ? "AI Launch Center"
                : "AI Foundation Installer";
            return "K-7 " + product + " — " + edition.edition_id;
        }

        public static SolidColorBrush WindowBackground(EditionProfile edition)
        {
            return new SolidColorBrush(
                edition.owner_controlled
                    ? Color.FromRgb(7, 30, 34)
                    : Color.FromRgb(246, 247, 245)
            );
        }
    }
}
