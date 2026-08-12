using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    internal sealed class EditionProfile
    {
        private static readonly string[] ExactProperties = new[]
        {
            "edition_id",
            "display_name",
            "distribution_allowed",
            "included_target_ids",
            "required_target_ids",
            "theme_id",
            "owner_controlled",
            "product_role"
        };

        public string edition_id { get; set; }
        public string display_name { get; set; }
        public bool distribution_allowed { get; set; }
        public string[] included_target_ids { get; set; }
        public string[] required_target_ids { get; set; }
        public string theme_id { get; set; }
        public bool owner_controlled { get; set; }
        public string product_role { get; set; }

        public static EditionProfile LoadEmbedded()
        {
            Stream resource = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream("EditionProfile.json");
            if (resource == null)
            {
                throw new InvalidOperationException(
                    "Embedded edition profile is missing"
                );
            }

            string json;
            using (resource)
            using (StreamReader reader = new StreamReader(
                resource,
                new UTF8Encoding(false),
                true
            ))
            {
                json = reader.ReadToEnd();
            }

            JavaScriptSerializer serializer = new JavaScriptSerializer();
            Dictionary<string, object> document;
            EditionProfile profile;
            try
            {
                document = serializer.Deserialize<
                    Dictionary<string, object>
                >(json);
                profile = serializer.Deserialize<EditionProfile>(json);
            }
            catch (Exception exception)
            {
                throw new InvalidOperationException(
                    "Embedded edition profile is invalid",
                    exception
                );
            }

            if (document == null ||
                !document.Keys.OrderBy(value => value, StringComparer.Ordinal)
                    .SequenceEqual(
                        ExactProperties.OrderBy(
                            value => value,
                            StringComparer.Ordinal
                        ),
                        StringComparer.Ordinal
                    ))
            {
                throw new InvalidOperationException(
                    "Embedded edition profile properties differ"
                );
            }

            profile.Validate();
            return profile;
        }

        public bool Includes(string targetId)
        {
            return included_target_ids.Contains(
                targetId,
                StringComparer.Ordinal
            );
        }

        public bool Requires(string targetId)
        {
            return required_target_ids.Contains(
                targetId,
                StringComparer.Ordinal
            );
        }

        public void Validate()
        {
            if (product_role != "Installer" &&
                product_role != "LaunchCenter")
            {
                throw new InvalidOperationException(
                    "Embedded product role is invalid"
                );
            }

            if (edition_id == "Employee")
            {
                ValidateExact(
                    "K-7 AI Foundation Employee",
                    true,
                    new[] { "claude", "codex", "opencode" },
                    new[] { "claude", "codex", "opencode" },
                    "K7Signal",
                    false
                );
                return;
            }

            if (edition_id == "Simple")
            {
                ValidateExact(
                    "K-7 AI Foundation Simple",
                    true,
                    new[] { "claude", "codex", "opencode" },
                    new[] { "claude", "codex", "opencode" },
                    "K7Signal",
                    false
                );
                return;
            }

            if (edition_id == "Owner")
            {
                ValidateExact(
                    "K-7 AI Foundation Owner",
                    false,
                    new[] { "claude", "codex", "opencode" },
                    new[] { "claude", "codex", "opencode" },
                    "SignalConsole",
                    true
                );
                return;
            }

            throw new InvalidOperationException(
                "Embedded edition id is invalid"
            );
        }

        private void ValidateExact(
            string expectedDisplayName,
            bool expectedDistributionAllowed,
            string[] expectedIncluded,
            string[] expectedRequired,
            string expectedTheme,
            bool expectedOwnerControlled
        )
        {
            if (display_name != expectedDisplayName ||
                distribution_allowed != expectedDistributionAllowed ||
                included_target_ids == null ||
                !included_target_ids.SequenceEqual(
                    expectedIncluded,
                    StringComparer.Ordinal
                ) ||
                required_target_ids == null ||
                !required_target_ids.SequenceEqual(
                    expectedRequired,
                    StringComparer.Ordinal
                ) ||
                theme_id != expectedTheme ||
                owner_controlled != expectedOwnerControlled)
            {
                throw new InvalidOperationException(
                    "Embedded edition contract differs"
                );
            }
        }
    }
}
