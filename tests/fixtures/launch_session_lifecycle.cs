using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Web.Script.Serialization;

namespace LlmFoundationInstaller
{
    public class LauncherSessionResult
    {
        public string status {get;set;} public string transport {get;set;} public bool uses_proxy {get;set;}
        public bool cleanup_verified {get;set;} public int process_exit_code {get;set;}
        public string target_id {get;set;} public List<string> lifecycle {get;set;} public string reason {get;set;}
    }
    public class SingBoxSessionResult
    {
        public string status {get;set;} public bool cleanup_verified {get;set;} public bool uses_proxy {get;set;}
        public bool secret_redacted {get;set;} public List<string> lifecycle {get;set;} public string reason {get;set;}
    }
    public class LaunchTargetResolution
    {
        public string status {get;set;} public string role {get;set;} public string target_id {get;set;}
    }
    public class EditionProfile
    {
        public string product_role {get;set;}
        public static EditionProfile LoadEmbedded() { return new EditionProfile(); }
    }
    public static class LaunchTargetResolver
    {
        public static LaunchTargetResolution Resolve(EditionProfile e,string b,string h,string target)
        { return new LaunchTargetResolution {status="RESOLVED",role="desktop",target_id=target}; }
    }
    public static class Original
    {
        private static Assembly core = Assembly.LoadFile(Environment.GetEnvironmentVariable("K7_FIXTURE_ORIGINAL"));
        public static object Call(string type,string method,params object[] args)
        {
            try {return core.GetType("LlmFoundationInstaller."+type).InvokeMember(method,
                BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static|BindingFlags.InvokeMethod,null,null,args);}
            catch(TargetInvocationException e) {throw e.InnerException??e;}
        }
        public static bool Clean(object result) {return (bool)result.GetType().GetProperty("cleanup_verified").GetValue(result,null);}
    }
    public static class ClientLauncher
    {
        private static object gate=new object();
        private static bool active;
        private static int injectedStopFailures;
        static ClientLauncher(){AppDomain.CurrentDomain.ProcessExit += delegate {StopActiveRoute();};}
        public static SingBoxSessionResult ResetManagedRoute(string home){return StopActiveRoute();}
        public static bool HasActiveRoute(){lock(gate)return active;}
        public static SingBoxSessionResult StopActiveRoute()
        {
            lock(gate)
            {
                bool clean=true;
                if(active)
                {
                    string fail=Environment.GetEnvironmentVariable("K7_FIXTURE_STOP_FAIL");
                    if(!String.IsNullOrEmpty(fail)&&Interlocked.Increment(ref injectedStopFailures)==1)
                    {
                        if(fail=="throw")throw new InvalidOperationException("INJECTED_STOP_FAILURE");
                        return new SingBoxSessionResult{status="FAILED",cleanup_verified=false,reason="INJECTED_STOP_FAILURE"};
                    }
                    clean=Original.Clean(Original.Call("SystemProxyLease","StopActiveRoute"));
                    clean=Original.Clean(Original.Call("SingBoxSession","RecoverOwnedSessions",Program.Root,Process.GetCurrentProcess().Id))&&clean;
                    active=false;
                }
                return new SingBoxSessionResult {status=clean?"PASS":"FAILED",cleanup_verified=clean,lifecycle=new List<string>()};
            }
        }
        public static LauncherSessionResult StartAndWait(LaunchTargetResolution t,string route,string bundle,string home)
        {
            if(Environment.GetEnvironmentVariable("K7_FIXTURE_FAIL")=="1")
                return new LauncherSessionResult{status="FAILED",cleanup_verified=true,target_id=t.target_id,reason="CLIENT_START_FAILED"};
            File.WriteAllText(Path.Combine(Program.Root,"worker.pid"),Process.GetCurrentProcess().Id.ToString());
            Process runtime=Program.Spawn("--fixture-runtime");
            Program.WaitFile("runtime.port");
            int port=Int32.Parse(File.ReadAllText(Path.Combine(Program.Root,"runtime.port")));
            string session=Path.Combine(Program.Root,".llm-foundation","launcher-state","sessions","test");
            Directory.CreateDirectory(session);
            string exe=Process.GetCurrentProcess().MainModule.FileName;
            string hash;
            using(var sha=SHA256.Create())using(var file=File.OpenRead(exe))hash=BitConverter.ToString(sha.ComputeHash(file)).Replace("-","").ToLowerInvariant();
            File.WriteAllText(Path.Combine(session,"owned-state.json"),new JavaScriptSerializer().Serialize(new {
                schema_version=1,nonce="fixture-nonce",owner_pid=Process.GetCurrentProcess().Id,process_id=runtime.Id,
                listen_port=port,executable_path=exe,executable_sha256=hash}));
            object lease=Original.Call("SystemProxyLease","Acquire",Program.Root,port,Program.RegistryKey);
            string status=(string)lease.GetType().GetProperty("status").GetValue(lease,null);
            if(status!="ACQUIRED")throw new Exception(new JavaScriptSerializer().Serialize(lease));
            Process client=Program.Spawn("--fixture-client");
            File.WriteAllText(Path.Combine(Program.Root,"client.pid"),client.Id.ToString());
            lock(gate) active=true;
            client.WaitForExit();
            SingBoxSessionResult stopped=StopActiveRoute();
            return new LauncherSessionResult{status="PASS",transport=route,uses_proxy=true,cleanup_verified=stopped.cleanup_verified,
                target_id=t.target_id,process_exit_code=0,lifecycle=new List<string>()};
        }
    }
    public static class LaunchCenterActions
    {
        public static LauncherSessionResult Launch(LaunchTargetResolution target)
        {return LaunchSessionHost.StartAndWait(target,"SingBoxHttp",AppDomain.CurrentDomain.BaseDirectory,Program.Root);}
    }
    public static class Program
    {
        public static string Root {get{return Environment.GetEnvironmentVariable("K7_FIXTURE_ROOT");}}
        public static string RegistryKey {get{return Environment.GetEnvironmentVariable("K7_FIXTURE_REGISTRY");}}
        public static void WriteOutput(string text){Console.WriteLine(text);}
        public static void WaitFile(string name)
        {var end=DateTime.UtcNow.AddSeconds(20);while(!File.Exists(Path.Combine(Root,name))){if(DateTime.UtcNow>end)throw new TimeoutException(name);Thread.Sleep(30);}}
        public static Process Spawn(string arg)
        {return Process.Start(new ProcessStartInfo{FileName=Process.GetCurrentProcess().MainModule.FileName,Arguments=arg,UseShellExecute=false,CreateNoWindow=true});}
        [STAThread]
        public static int Main(string[] args)
        {
            if(args[0]=="--launch-session")return LaunchSessionHost.Run(EditionProfile.LoadEmbedded(),AppDomain.CurrentDomain.BaseDirectory,args);
            if(args[0]=="--system-proxy-watchdog")
            {Original.Call("SystemProxyLease","Watchdog",Int32.Parse(args[1]),args[2],args[3]);return 0;}
            if(args[0]=="--fixture-runtime")
            {
                var server=new TcpListener(IPAddress.Loopback,0);server.Start();
                File.WriteAllText(Path.Combine(Root,"runtime.pid"),Process.GetCurrentProcess().Id.ToString());
                File.WriteAllText(Path.Combine(Root,"runtime.port"),((IPEndPoint)server.LocalEndpoint).Port.ToString());
                while(true){using(var c=server.AcceptTcpClient())using(var s=c.GetStream()){byte[] b=System.Text.Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK");s.Write(b,0,b.Length);}}
            }
            if(args[0]=="--fixture-client"){WaitFile("client-exit");return 0;}
            Directory.CreateDirectory(Root);
            LauncherSessionResult result=null;
            var target=LaunchTargetResolver.Resolve(null,null,null,"codex-desktop");
            Thread uiTask=new Thread(delegate(){result=LaunchCenterActions.Launch(target);});uiTask.Start();
            while(uiTask.IsAlive&&!LaunchSessionHost.IsActive())Thread.Sleep(30);
            if(uiTask.IsAlive)
            {
                File.WriteAllText(Path.Combine(Root,"ui-ready"),"ready");
                if(args[1]=="close")Environment.Exit(0);
                if(args[1]=="repeat")
                {
                    var repeated=LaunchCenterActions.Launch(target);
                    File.WriteAllText(Path.Combine(Root,"repeat.json"),new JavaScriptSerializer().Serialize(repeated));
                }
                if(args[1]=="stop"||args[1]=="repeat"||args[1]=="reset")
                {
                    var stopped=args[1]=="reset"?LaunchSessionHost.ResetManagedRoute(Root):LaunchSessionHost.StopActiveRoute();
                    File.WriteAllText(Path.Combine(Root,"stop.json"),new JavaScriptSerializer().Serialize(stopped));
                    File.WriteAllText(Path.Combine(Root,"client-exit"),"");
                }
            }
            uiTask.Join();
            File.WriteAllText(Path.Combine(Root,"result.json"),new JavaScriptSerializer().Serialize(result));
            return result!=null&&result.status=="PASS"?0:20;
        }
    }
}
