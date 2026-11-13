using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;

namespace MAILauncher;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new MainWindow());
    }
}

internal sealed class MainWindow : Form
{
    private readonly TextBox log = new()
    {
        Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical,
        Dock = DockStyle.Fill, Font = new Font("Consolas", 9.5f)
    };
    private readonly Label status = new()
    {
        AutoSize = true, Text = "Ready · MAI host",
        Padding = new Padding(8)
    };
    private readonly string root = AppContext.BaseDirectory;
    private readonly object processSync = new();
    private Process? active;
    private readonly List<Button> commandButtons = [];

    public MainWindow()
    {
        Text = "MAI — Portable Launcher";
        Width = 910; Height = 610; MinimumSize = new Size(680, 420);
        StartPosition = FormStartPosition.CenterScreen;
        var top = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(8) };
        foreach (var (caption, command) in new[] {
            ("Start", "start"), ("Stop", "end"), ("Restart", "restart"),
            ("Check", "check"), ("Setup", "setup"), ("Update llama.cpp", "update"),
            ("Update Python", "update-python") })
        {
            var button = new Button { Text = caption, AutoSize = true, MinimumSize = new Size(85, 34) };
            button.Click += async (_, _) => await RunCommandAsync(command);
            top.Controls.Add(button);
            commandButtons.Add(button);
        }
        var bottom = new FlowLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true };
        var web = new Button { Text = "Open MAI Web UI", AutoSize = true };
        web.Click += (_, _) => Process.Start(new ProcessStartInfo("http://127.0.0.1:8000") { UseShellExecute = true });
        bottom.Controls.Add(web);
        bottom.Controls.Add(status);
        Controls.Add(log);
        Controls.Add(bottom);
        Controls.Add(top);
        WriteLine("MAI launcher folder: " + root);
        if (!File.Exists(Path.Combine(root, "mai.ps1")))
            WriteLine("ERROR: mai.ps1 not found next to MAI.exe. Run the distribution builder.");
    }

    private void WriteLine(string message)
    {
        if (IsDisposed) return;
        if (InvokeRequired)
        {
            try { BeginInvoke(() => WriteLine(message)); } catch (InvalidOperationException) { }
            return;
        }
        log.AppendText(message + Environment.NewLine);
    }

    private async Task RunCommandAsync(string command)
    {
        var script = Path.Combine(root, "mai.ps1");
        if (!File.Exists(script))
        {
            WriteLine("Missing MAI script: " + script);
            return;
        }
        lock (processSync)
        {
            if (command is not ("end" or "restart") && active is { HasExited: false })
            {
                WriteLine("Another MAI launcher command is still running. Close it only after the command finishes.");
                return;
            }
        }

        // Execute the same trusted, repository-owned launcher; no dynamic commands,
        // scripts or model-supplied parameters are evaluated.
        var info = new ProcessStartInfo
        {
            FileName = "powershell.exe", UseShellExecute = false,
            WorkingDirectory = root, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        info.ArgumentList.Add("-NoProfile");
        info.ArgumentList.Add("-ExecutionPolicy");
        info.ArgumentList.Add("Bypass");
        info.ArgumentList.Add("-File");
        info.ArgumentList.Add(script);
        info.ArgumentList.Add(command);
        var process = new Process { StartInfo = info, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, e) => { if (e.Data != null) WriteLine(e.Data); };
        process.ErrorDataReceived += (_, e) => { if (e.Data != null) WriteLine("ERR: " + e.Data); };

        try
        {
            if (command == "start") lock (processSync) active = process;
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            WriteLine("Command: " + command);
            status.Text = "Running: " + command;
            // Start may remain running for the lifetime of the MAI server;
            // do not block the UI thread waiting for it.
            await process.WaitForExitAsync();
            WriteLine("Command finished: " + command + " (exit " + process.ExitCode + ")");
            status.Text = process.ExitCode == 0 ? "Ready" : "Command failed";
        }
        catch (Exception ex) { WriteLine("Launcher error: " + ex.Message); status.Text = "Error"; }
        finally
        {
            lock (processSync) { if (ReferenceEquals(active, process)) active = null; }
            process.Dispose();
        }
    }
}
