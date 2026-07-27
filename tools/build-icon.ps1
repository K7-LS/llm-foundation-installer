[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
Set-StrictMode -Version 2.0

Add-Type -AssemblyName System.Drawing

function New-RoundedRectanglePath {
    param(
        [Parameter(Mandatory = $true)][Drawing.RectangleF]$Bounds,
        [Parameter(Mandatory = $true)][single]$Radius
    )

    $Diameter = [single]($Radius * 2)
    $Path = New-Object Drawing.Drawing2D.GraphicsPath
    $Path.AddArc(
        $Bounds.Left,
        $Bounds.Top,
        $Diameter,
        $Diameter,
        180,
        90
    )
    $Path.AddArc(
        $Bounds.Right - $Diameter,
        $Bounds.Top,
        $Diameter,
        $Diameter,
        270,
        90
    )
    $Path.AddArc(
        $Bounds.Right - $Diameter,
        $Bounds.Bottom - $Diameter,
        $Diameter,
        $Diameter,
        0,
        90
    )
    $Path.AddArc(
        $Bounds.Left,
        $Bounds.Bottom - $Diameter,
        $Diameter,
        $Diameter,
        90,
        90
    )
    $Path.CloseFigure()
    return $Path
}

function New-IconPng {
    param([Parameter(Mandatory = $true)][int]$Size)

    $Bitmap = New-Object Drawing.Bitmap($Size, $Size)
    $Bitmap.SetResolution(96, 96)
    $Graphics = [Drawing.Graphics]::FromImage($Bitmap)
    try {
        $Graphics.Clear([Drawing.Color]::Transparent)
        $Graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $Graphics.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $Graphics.CompositingQuality = (
            [Drawing.Drawing2D.CompositingQuality]::HighQuality
        )

        $Padding = [single][Math]::Max(1, $Size * 0.035)
        $Bounds = New-Object Drawing.RectangleF(
            $Padding,
            $Padding,
            [single]($Size - (2 * $Padding)),
            [single]($Size - (2 * $Padding))
        )
        $Radius = [single]($Size * 0.22)
        $Shape = New-RoundedRectanglePath -Bounds $Bounds -Radius $Radius
        try {
            $BrandBrush = New-Object Drawing.Drawing2D.LinearGradientBrush(
                $Bounds,
                [Drawing.Color]::FromArgb(255, 78, 113, 235),
                [Drawing.Color]::FromArgb(255, 49, 82, 199),
                45
            )
            try {
                $Graphics.FillPath($BrandBrush, $Shape)
            } finally {
                $BrandBrush.Dispose()
            }
        } finally {
            $Shape.Dispose()
        }

        $Stroke = [single][Math]::Max(1.25, $Size * 0.055)
        $WhitePen = New-Object Drawing.Pen(
            [Drawing.Color]::White,
            $Stroke
        )
        try {
            $WhitePen.StartCap = [Drawing.Drawing2D.LineCap]::Round
            $WhitePen.EndCap = [Drawing.Drawing2D.LineCap]::Round
            $WhitePen.LineJoin = [Drawing.Drawing2D.LineJoin]::Round
            $Circle = New-Object Drawing.RectangleF(
                [single]($Size * 0.27),
                [single]($Size * 0.27),
                [single]($Size * 0.46),
                [single]($Size * 0.46)
            )
            $Graphics.DrawEllipse($WhitePen, $Circle)
            [Drawing.PointF[]]$Check = @(
                (New-Object Drawing.PointF(
                    [single]($Size * 0.31),
                    [single]($Size * 0.53)
                )),
                (New-Object Drawing.PointF(
                    [single]($Size * 0.46),
                    [single]($Size * 0.66)
                )),
                (New-Object Drawing.PointF(
                    [single]($Size * 0.73),
                    [single]($Size * 0.35)
                ))
            )
            $Graphics.DrawLines($WhitePen, $Check)
        } finally {
            $WhitePen.Dispose()
        }

        $Stream = New-Object IO.MemoryStream
        try {
            $Bitmap.Save(
                $Stream,
                [Drawing.Imaging.ImageFormat]::Png
            )
            return $Stream.ToArray()
        } finally {
            $Stream.Dispose()
        }
    } finally {
        $Graphics.Dispose()
        $Bitmap.Dispose()
    }
}

$Images = @(
    foreach ($Size in @(16, 24, 32, 48, 64, 128, 256)) {
        [pscustomobject]@{
            size = $Size
            bytes = New-IconPng -Size $Size
        }
    }
)

$FullOutput = [IO.Path]::GetFullPath($OutputPath)
$Parent = [IO.Path]::GetDirectoryName($FullOutput)
if ([string]::IsNullOrWhiteSpace($Parent)) {
    throw 'OutputPath must include a parent directory'
}
[IO.Directory]::CreateDirectory($Parent) | Out-Null

$Stream = New-Object IO.MemoryStream
$Writer = New-Object IO.BinaryWriter($Stream)
try {
    $Writer.Write([uint16]0)
    $Writer.Write([uint16]1)
    $Writer.Write([uint16]$Images.Count)

    [uint32]$Offset = 6 + (16 * $Images.Count)
    foreach ($Image in $Images) {
        $Dimension = if ($Image.size -eq 256) {
            [byte]0
        } else {
            [byte]$Image.size
        }
        $Writer.Write($Dimension)
        $Writer.Write($Dimension)
        $Writer.Write([byte]0)
        $Writer.Write([byte]0)
        $Writer.Write([uint16]1)
        $Writer.Write([uint16]32)
        $Writer.Write([uint32]$Image.bytes.Length)
        $Writer.Write($Offset)
        $Offset += [uint32]$Image.bytes.Length
    }
    foreach ($Image in $Images) {
        $Writer.Write([byte[]]$Image.bytes)
    }
    $Writer.Flush()
    [IO.File]::WriteAllBytes($FullOutput, $Stream.ToArray())
} finally {
    $Writer.Dispose()
    $Stream.Dispose()
}

Write-Output $FullOutput
