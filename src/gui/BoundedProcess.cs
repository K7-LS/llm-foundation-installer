using System;
using System.Diagnostics;
using System.Threading.Tasks;

namespace LlmFoundationInstaller
{
    internal sealed class BoundedProcessResult
    {
        public bool started { get; set; }
        public bool timed_out { get; set; }
        public bool drained { get; set; }
        public int exit_code { get; set; }
        public string standard_output { get; set; }
        public string standard_error { get; set; }

        public bool Succeeded
        {
            get
            {
                return started && !timed_out && drained && exit_code == 0;
            }
        }
    }

    // Общий ограниченный запуск процесса с перехватом обоих потоков.
    // Замечание Codex к плану переработки (2026-09-02): 23 синхронных
    // ReadToEnd перед WaitForExit(timeout) делали таймауты фиктивными —
    // повисший дочерний процесс держал stdout, ReadToEnd не возвращался,
    // а переполненный stderr во время чтения stdout давал pipe-deadlock.
    // Образец — запуск движка в FoundationWorkflow (PR #60).
    internal static class BoundedProcess
    {
        internal const int DrainMilliseconds = 10000;

        public static BoundedProcessResult Run(
            ProcessStartInfo start,
            int timeoutMilliseconds
        )
        {
            start.UseShellExecute = false;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            BoundedProcessResult result = new BoundedProcessResult
            {
                standard_output = "",
                standard_error = "",
                exit_code = -1
            };
            using (Process process = Process.Start(start))
            {
                if (process == null)
                {
                    return result;
                }
                result.started = true;
                Task<string> outputTask =
                    process.StandardOutput.ReadToEndAsync();
                Task<string> errorTask =
                    process.StandardError.ReadToEndAsync();
                bool exited = process.WaitForExit(timeoutMilliseconds);
                if (!exited)
                {
                    try
                    {
                        process.Kill();
                    }
                    catch
                    {
                    }
                }
                // После Kill потоки закрываются; ограничение — страховка от
                // внука, унаследовавшего дескрипторы.
                result.drained = Task.WaitAll(
                    new Task[] { outputTask, errorTask },
                    DrainMilliseconds
                );
                result.timed_out = !exited;
                result.standard_output =
                    outputTask.IsCompleted && !outputTask.IsFaulted
                        ? outputTask.Result
                        : "";
                result.standard_error =
                    errorTask.IsCompleted && !errorTask.IsFaulted
                        ? errorTask.Result
                        : "";
                if (exited)
                {
                    result.exit_code = process.ExitCode;
                }
                return result;
            }
        }
    }
}
