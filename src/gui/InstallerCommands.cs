using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using static LlmFoundationInstaller.Program;

namespace LlmFoundationInstaller
{
    // Обработчик CLI-команды: args[0] — имя команды, дальше — её аргументы.
    internal delegate int CommandHandler(
        EditionProfile edition,
        string bundleRoot,
        string[] args
    );

    internal sealed class CliCommandRecord
    {
        public string name;
        // product — точку зовёт сам EXE; tool — hub_canary, диагностика,
        // сборка; test — только тесты (собирается с K7_TEST_HOOKS).
        public string kind;
        // Число аргументов после имени команды.
        public int min_args;
        public int max_args;
    }

    internal sealed class CliCommandTable
    {
        public bool test_hooks;
        public List<CliCommandRecord> commands;
    }

    // Таблица CLI-команд EXE (этап 4 плана переработки). Продуктовые и
    // инструментальные обработчики — здесь; test-only — InstallerTestHost.cs:
    // их регистрирует partial-метод RegisterTestHost, который существует
    // только в сборке с K7_TEST_HOOKS (tools/build-gui.ps1 -TestHooks).
    internal static partial class InstallerCommands
    {
        // Обработчик просит Main открыть окно (--launch-center-ui).
        internal const int ContinueToUi = -1;

        private sealed class Entry
        {
            public CliCommandRecord record;
            public CommandHandler handler;
        }

        private static readonly List<Entry> Commands = new List<Entry>();

        static InstallerCommands()
        {
            Register("--catalog-json", "tool", 0, 0, CatalogJson);
            Register("--commands-json", "tool", 0, 0, CommandsJson);
            Register("--ensure-runtime-json", "tool", 1, 1, EnsureRuntimeJson);
            Register("--launch-center-product-json", "tool", 0, 0, LaunchCenterProductJson);
            Register("--launch-center-ui", "tool", 0, 0, LaunchCenterUi);
            Register("--product-json", "tool", 0, 0, ProductJson);
            Register("--resolve-launch-target-json", "tool", 2, 2, ResolveLaunchTargetJson);
            Register("--self-test-json", "tool", 0, 0, SelfTestJson);
            Register("--system-proxy-watchdog", "product", 2, 3, SystemProxyWatchdog);
            Register("--workflow-json", "tool", 4, 4, WorkflowJson);
            RegisterTestHost();
            Commands.Sort(delegate (Entry left, Entry right)
            {
                return String.CompareOrdinal(
                    left.record.name,
                    right.record.name
                );
            });
        }

        static partial void RegisterTestHost();

        private static void Register(
            string name,
            string kind,
            int minArgs,
            int maxArgs,
            CommandHandler handler)
        {
            Commands.Add(new Entry
            {
                record = new CliCommandRecord
                {
                    name = name,
                    kind = kind,
                    min_args = minArgs,
                    max_args = maxArgs
                },
                handler = handler
            });
        }

        // false — команда неизвестна или число аргументов не совпало: Main
        // отвечает «Неподдерживаемая команда» с кодом 2, как и раньше.
        internal static bool TryRun(
            EditionProfile edition,
            string bundleRoot,
            string[] args,
            out int exitCode)
        {
            exitCode = 2;
            if (args.Length == 0)
            {
                return false;
            }
            Entry entry = Commands.Find(delegate (Entry candidate)
            {
                return candidate.record.name == args[0];
            });
            int count = args.Length - 1;
            if (entry == null ||
                count < entry.record.min_args ||
                count > entry.record.max_args)
            {
                return false;
            }
            exitCode = entry.handler(edition, bundleRoot, args);
            return true;
        }

        private static int CatalogJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                ProductCatalog.Inspect(bundleRoot)
            ));
            return 0;
        }

        // Машинно-читаемая таблица команд: контракт гейта релизной
        // поверхности (tests/test_cli_surface.py) и таблицы в README.
        private static int CommandsJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            CliCommandTable table = new CliCommandTable
            {
                test_hooks = false,
                commands = new List<CliCommandRecord>()
            };
            foreach (Entry entry in Commands)
            {
                table.commands.Add(entry.record);
                if (entry.record.kind == "test")
                {
                    table.test_hooks = true;
                }
            }
            WriteOutput(new JavaScriptSerializer().Serialize(table));
            return 0;
        }

        private static int EnsureRuntimeJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            RuntimeBootstrapResult runtime =
                RuntimeBootstrap.EnsureInstalled(
                    bundleRoot,
                    args[1]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                runtime
            ));
            return runtime.status == "VERIFIED" ? 0 : 20;
        }

        private static int LaunchCenterProductJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            edition.product_role = "LaunchCenter";
            WriteOutput(new JavaScriptSerializer().Serialize(
                LaunchTargetCatalog.Describe(
                    edition,
                    bundleRoot
                )
            ));
            return 0;
        }

        private static int LaunchCenterUi(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            edition.product_role = "LaunchCenter";
            return ContinueToUi;
        }

        private static int ProductJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            WriteOutput(new JavaScriptSerializer().Serialize(
                LaunchTargetCatalog.Describe(
                    edition,
                    bundleRoot
                )
            ));
            return 0;
        }

        private static int ResolveLaunchTargetJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            LaunchTargetResolution resolution =
                LaunchTargetResolver.Resolve(
                    edition,
                    bundleRoot,
                    args[1],
                    args[2]
                );
            WriteOutput(new JavaScriptSerializer().Serialize(
                resolution
            ));
            return resolution.status == "RESOLVED" ? 0 : 20;
        }

        private static int SelfTestJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            int protocol;
            bool validated = BundleIntegrity.ValidateEngine(bundleRoot, out protocol);
            bool platformReady =
                PlatformCompatibility.Inspect().status == "READY";
            Dictionary<string, object> payload = new Dictionary<string, object>();
            payload["app_id"] = "llm-foundation-installer";
            payload["engine_validated"] = validated;
            payload["foundation_protocol"] = protocol;
            payload["network"] = "user-initiated-only";
            payload["automatic_network"] = false;
            payload["reverse_flow"] = false;
            payload["targets"] = ProductCatalog.TargetIds();
            payload["telemetry"] = false;
            payload["version"] = BundleIntegrity.ReadBundleVersion(bundleRoot);
            WriteOutput(new JavaScriptSerializer().Serialize(payload));
            return validated && platformReady ? 0 : 30;
        }

        private static int SystemProxyWatchdog(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            int ownerPid;
            if (!Int32.TryParse(args[1], out ownerPid))
            {
                WriteError("Некорректный PID владельца");
                return 2;
            }
            ProxyRecoveryResult watchdogResult;
            if (args.Length == 4)
            {
                if (!ClientBootstrap.Load(bundleRoot).test_only ||
                    !SystemProxyLease
                        .IsAllowedTestRegistrySubkey(args[3]))
                {
                    WriteError(
                        "Тест системного proxy запрещён для production"
                    );
                    return 2;
                }
                watchdogResult = SystemProxyLease.Watchdog(
                    ownerPid,
                    args[2],
                    args[3]
                );
            }
            else
            {
                watchdogResult = SystemProxyLease.Watchdog(
                    ownerPid,
                    args[2]
                );
            }
            WriteOutput(new JavaScriptSerializer().Serialize(
                watchdogResult
            ));
            return watchdogResult.cleanup_verified ? 0 : 20;
        }

        private static int WorkflowJson(
            EditionProfile edition,
            string bundleRoot,
            string[] args)
        {
            string output;
            string error;
            int exitCode = FoundationWorkflow.Run(
                bundleRoot,
                args[1],
                args[2],
                args[3],
                args[4],
                out output,
                out error
            );
            if (!String.IsNullOrWhiteSpace(output))
            {
                WriteOutput(output.TrimEnd('\r', '\n'));
            }
            if (!String.IsNullOrWhiteSpace(error))
            {
                WriteError(error.TrimEnd('\r', '\n'));
            }
            return exitCode;
        }
    }
}
