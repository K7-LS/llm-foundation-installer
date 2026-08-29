using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    // Общий загрузчик встроенных JSON-контрактов (кандидат №5 этапа 2а):
    // ресурс → UTF-8 → двойная десериализация (словарь для точной сверки
    // набора ключей + типизированный объект). Validate() остаётся за
    // вызывающим типом.
    internal static class EmbeddedJson
    {
        public static T Load<T>(
            string resourceName,
            string[] exactProperties,
            string subject
        )
        {
            Stream resource = Assembly.GetExecutingAssembly()
                .GetManifestResourceStream(resourceName);
            if (resource == null)
            {
                throw new InvalidOperationException(
                    subject + " is missing"
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
            T value;
            try
            {
                document = serializer.Deserialize<
                    Dictionary<string, object>
                >(json);
                value = serializer.Deserialize<T>(json);
            }
            catch (Exception exception)
            {
                throw new InvalidOperationException(
                    subject + " is invalid",
                    exception
                );
            }

            if (document == null ||
                !document.Keys.OrderBy(name => name, StringComparer.Ordinal)
                    .SequenceEqual(
                        exactProperties.OrderBy(
                            name => name,
                            StringComparer.Ordinal
                        ),
                        StringComparer.Ordinal
                    ))
            {
                throw new InvalidOperationException(
                    subject + " properties differ"
                );
            }

            return value;
        }
    }
}
