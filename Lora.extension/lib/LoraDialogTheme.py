
# -*- coding: utf-8 -*-
"""Lora-themed WPF dialogs for pyRevit tools."""
import clr
import System
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
from System.Windows.Markup import XamlReader
from System.Windows import MessageBoxButton, MessageBoxImage, SizeToContent, WindowStartupLocation, ResizeMode, TextWrapping
from System.Windows.Controls import SelectionMode
from System.Windows.Media.Animation import DoubleAnimation, RepeatBehavior
from System import TimeSpan

def lora_select_from_list(options, title="Lora", button_name="Continue",
                          multiselect=False, width=500, height=330, **kwargs):
    """Themed replacement for common pyRevit SelectFromList dialogs."""
    if options is None:
        return None
    values = list(options)
    # Preserve string labels and return original objects for selected rows.
    labels = [str(x) for x in values]
    xaml = r"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Lora" Height="360" Width="520" MinHeight="260" MinWidth="420"
        WindowStartupLocation="CenterScreen" ResizeMode="CanResize"
        Background="#151C2D" Foreground="#F3F5FF" FontFamily="Segoe UI" FontSize="13">
  <Grid ClipToBounds="True">
    <Canvas IsHitTestVisible="False">
      <Ellipse x:Name="GlowOne" Width="240" Height="240" Canvas.Left="300" Canvas.Top="-100" Fill="#713FE0" Opacity="0.08"/>
      <Ellipse x:Name="GlowTwo" Width="180" Height="180" Canvas.Left="-80" Canvas.Top="210" Fill="#E94F9B" Opacity="0.05"/>
    </Canvas>
    <Grid Margin="20,18,20,18">
      <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <StackPanel Grid.Row="0" Margin="0,0,0,12">
        <TextBlock Text="LORA  /  DESIGN • BIM" Foreground="#B18CFF" FontSize="10" FontWeight="Bold"/>
        <TextBlock x:Name="Heading" Text="Select an option" FontSize="22" FontWeight="SemiBold" Margin="0,4,0,0"/>
      </StackPanel>
      <TextBox x:Name="SearchBox" Grid.Row="1" Height="34" Margin="0,0,0,10"
               Padding="10,6" Background="#202A40" Foreground="#F3F5FF"
               BorderBrush="#596584" CaretBrush="#B18CFF" BorderThickness="1"/>
      <ListBox x:Name="OptionsList" Grid.Row="2" Background="#1B2437" Foreground="#F3F5FF"
               BorderBrush="#45516F" BorderThickness="1" Padding="5">
        <ListBox.ItemContainerStyle>
          <Style TargetType="{x:Type ListBoxItem}">
            <Setter Property="Padding" Value="9,7"/>
            <Setter Property="Margin" Value="0,1"/>
            <Setter Property="Foreground" Value="#F3F5FF"/>
            <Setter Property="Background" Value="#1B2437"/>
            <Setter Property="Template">
              <Setter.Value>
                <ControlTemplate TargetType="{x:Type ListBoxItem}">
                  <Border x:Name="Row" Background="{TemplateBinding Background}" CornerRadius="5" Padding="{TemplateBinding Padding}">
                    <ContentPresenter/>
                  </Border>
                  <ControlTemplate.Triggers>
                    <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="Row" Property="Background" Value="#2A3550"/></Trigger>
                    <Trigger Property="IsSelected" Value="True"><Setter TargetName="Row" Property="Background" Value="#382D5B"/><Setter Property="Foreground" Value="#FFFFFF"/></Trigger>
                  </ControlTemplate.Triggers>
                </ControlTemplate>
              </Setter.Value>
            </Setter>
          </Style>
        </ListBox.ItemContainerStyle>
      </ListBox>
      <Grid Grid.Row="3" Margin="0,12,0,0">
        <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="125"/></Grid.ColumnDefinitions>
        <TextBlock Text="VIOLET • NAVY • MINIMAL" Foreground="#7886A5" FontSize="10" VerticalAlignment="Center"/>
        <Button x:Name="ContinueButton" Grid.Column="1" Content="Continue" Height="36"
                Background="#8057E8" Foreground="White" BorderBrush="#A783FF" BorderThickness="1"
                FontWeight="SemiBold" Cursor="Hand"/>
      </Grid>
    </Grid>
    <Rectangle Height="3" VerticalAlignment="Top" Fill="#8D5BFF"/>
  </Grid>
</Window>
"""
    window = XamlReader.Parse(xaml)
    window.Title = title
    window.Width = width
    window.Height = height
    window.FindName("Heading").Text = title
    lb = window.FindName("OptionsList")
    # WPF expects the SelectionMode enum, not a numeric value.
    lb.SelectionMode = SelectionMode.Extended if multiselect else SelectionMode.Single
    for label in labels:
        lb.Items.Add(label)
    result = {"items": None}
    search = window.FindName("SearchBox")
    def filter_items(sender, args):
        query = (search.Text or "").lower()
        selected_labels = []
        try:
            selected_labels = [str(x) for x in lb.SelectedItems]
        except Exception:
            pass
        lb.Items.Clear()
        for label in labels:
            if query in label.lower():
                lb.Items.Add(label)
                if label in selected_labels:
                    try:
                        lb.SelectedItems.Add(label)
                    except Exception:
                        pass
    search.TextChanged += filter_items
    def accept(sender, args):
        chosen = list(lb.SelectedItems)
        if not chosen and not multiselect and lb.SelectedItem is not None:
            chosen = [lb.SelectedItem]
        if chosen:
            result["items"] = [values[labels.index(str(x))] for x in chosen]
        window.DialogResult = True
        window.Close()
    window.FindName("ContinueButton").Content = button_name
    window.FindName("ContinueButton").Click += accept
    # Show the modal dialog so callers can make a selection.
    window.ShowDialog()
    picked = result["items"]
    if picked is None:
        return None
    return picked if multiselect else picked[0]


def lora_checklist_from_list(options, title="Select items", button_name="Continue",
                             width=520, height=460, check_all=True, **kwargs):
    """Themed checkbox picker with persistent filtering and Check All/Uncheck All."""
    if options is None:
        return None
    from System.Windows import Window, Thickness, HorizontalAlignment, VerticalAlignment, SizeToContent, WindowStartupLocation, ResizeMode
    from System.Windows.Controls import (Grid, RowDefinition, ColumnDefinition, StackPanel, TextBlock,
        TextBox, Button, Border, ScrollViewer, CheckBox)
    from System.Windows.Media import SolidColorBrush, Color
    values = list(options)
    win = Window()
    win.Title = title
    win.Width = width
    win.Height = height
    win.MinWidth = 420
    win.MinHeight = 300
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen
    win.ResizeMode = ResizeMode.CanResize
    win.Background = SolidColorBrush(Color.FromRgb(21, 28, 45))
    win.Foreground = SolidColorBrush(Color.FromRgb(243, 245, 255))
    root = Grid()
    root.Margin = Thickness(18)
    for h in ("Auto", "Auto", "*", "Auto"):
        rd = RowDefinition()
        rd.Height = System.Windows.GridLength.Auto if h == "Auto" else System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        root.RowDefinitions.Add(rd)
    eyebrow = TextBlock(); eyebrow.Text = "LORA  /  DESIGN • BIM"; eyebrow.Foreground = SolidColorBrush(Color.FromRgb(177,140,255)); eyebrow.FontSize = 10; eyebrow.FontWeight = System.Windows.FontWeights.Bold
    root.Children.Add(eyebrow); Grid.SetRow(eyebrow,0)
    heading = TextBlock(); heading.Text = title; heading.FontSize = 21; heading.FontWeight = System.Windows.FontWeights.SemiBold; heading.Margin = Thickness(0,5,0,12)
    root.Children.Add(heading); Grid.SetRow(heading,1)
    body = Grid()
    body_row1 = RowDefinition(); body_row1.Height = System.Windows.GridLength.Auto; body.RowDefinitions.Add(body_row1)
    body_row2 = RowDefinition(); body_row2.Height = System.Windows.GridLength(1, System.Windows.GridUnitType.Star); body.RowDefinitions.Add(body_row2)
    search = TextBox(); search.Height=34; search.Padding=Thickness(9,5,9,5); search.Margin=Thickness(0,0,0,8); search.Background=SolidColorBrush(Color.FromRgb(32,42,64)); search.Foreground=SolidColorBrush(Color.FromRgb(243,245,255)); search.BorderBrush=SolidColorBrush(Color.FromRgb(89,101,132))
    body.Children.Add(search); Grid.SetRow(search,0)
    scroll = ScrollViewer(); scroll.VerticalScrollBarVisibility=System.Windows.Controls.ScrollBarVisibility.Auto; scroll.Background=SolidColorBrush(Color.FromRgb(27,36,55)); scroll.BorderBrush=SolidColorBrush(Color.FromRgb(69,81,111)); scroll.BorderThickness=Thickness(1)
    stack = StackPanel(); stack.Margin=Thickness(8); scroll.Content=stack; body.Children.Add(scroll); Grid.SetRow(scroll,1)
    root.Children.Add(body); Grid.SetRow(body,2)
    row = StackPanel(); row.Orientation=System.Windows.Controls.Orientation.Horizontal; row.Margin=Thickness(0,12,0,0); row.HorizontalAlignment=HorizontalAlignment.Stretch
    if check_all:
        all_btn=Button(); all_btn.Content="Check All"; all_btn.Width=92; all_btn.Height=34; all_btn.Margin=Thickness(0,0,8,0); all_btn.Background=SolidColorBrush(Color.FromRgb(32,42,64)); all_btn.Foreground=SolidColorBrush(Color.FromRgb(243,245,255)); all_btn.BorderBrush=SolidColorBrush(Color.FromRgb(89,101,132)); row.Children.Add(all_btn)
        clear_btn=Button(); clear_btn.Content="Clear All"; clear_btn.Width=92; clear_btn.Height=34; clear_btn.Margin=Thickness(0,0,8,0); clear_btn.Background=SolidColorBrush(Color.FromRgb(32,42,64)); clear_btn.Foreground=SolidColorBrush(Color.FromRgb(243,245,255)); clear_btn.BorderBrush=SolidColorBrush(Color.FromRgb(89,101,132)); row.Children.Add(clear_btn)
    else:
        all_btn=None; clear_btn=None
    spacer=TextBlock(); spacer.Text="Select items individually"; spacer.Foreground=SolidColorBrush(Color.FromRgb(120,134,165)); spacer.VerticalAlignment=VerticalAlignment.Center; spacer.Margin=Thickness(0,0,8,0); row.Children.Add(spacer)
    go=Button(); go.Content=button_name; go.Width=125; go.Height=36; go.HorizontalAlignment=HorizontalAlignment.Right; go.Background=SolidColorBrush(Color.FromRgb(128,87,232)); go.Foreground=SolidColorBrush(Color.FromRgb(255,255,255)); go.BorderBrush=SolidColorBrush(Color.FromRgb(167,131,255)); row.Children.Add(go)
    root.Children.Add(row); Grid.SetRow(row,3)
    win.Content=root
    checks=[]
    for i, value in enumerate(values):
        # Parse each WPF CheckBox from XAML rather than invoking the CLR constructor.
        # This avoids IronPython/pythonnet CreateInstance overload differences across pyRevit engines.
        cb = XamlReader.Parse(
            '<CheckBox xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
            'Content="{}" Margin="4,6,4,6" Padding="3,2" FontSize="13" />'.format(
                str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
            )
        )
        cb.Tag = i
        cb.Foreground = SolidColorBrush(Color.FromRgb(243,245,255))
        stack.Children.Add(cb)
        checks.append(cb)
    def filter_rows(sender=None, args=None):
        query=(search.Text or "").lower()
        for cb in checks:
            cb.Visibility = System.Windows.Visibility.Visible if query in str(cb.Content).lower() else System.Windows.Visibility.Collapsed
    search.TextChanged += filter_rows
    if all_btn is not None:
        def set_all(sender,args):
            for cb in checks:
                if cb.Visibility == System.Windows.Visibility.Visible: cb.IsChecked=True
        def clear_all(sender,args):
            for cb in checks:
                if cb.Visibility == System.Windows.Visibility.Visible: cb.IsChecked=False
        all_btn.Click += set_all; clear_btn.Click += clear_all
    result={"items":None}
    def accept(sender,args):
        result["items"]=[values[int(cb.Tag)] for cb in checks if cb.IsChecked]
        win.DialogResult=True; win.Close()
    go.Click += accept
    win.ShowDialog()
    return result["items"]

def lora_alert(message, title="Lora", warn_icon=False, exitscript=False, **kwargs):
    """Dark themed alert dialog. Supports common forms.alert arguments."""
    from System.Windows import Window, VerticalAlignment, HorizontalAlignment
    from System.Windows.Controls import Grid, StackPanel, TextBlock, Button, Border
    from System.Windows import Thickness
    from System.Windows.Media import SolidColorBrush, Color
    win = Window()
    win.Title = title
    win.Width = 500
    win.SizeToContent = SizeToContent.Height
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen
    win.ResizeMode = ResizeMode.NoResize
    win.Background = SolidColorBrush(Color.FromRgb(21,28,45))
    win.Foreground = SolidColorBrush(Color.FromRgb(243,245,255))
    root = StackPanel()
    root.Margin = Thickness(22)
    eyebrow = TextBlock()
    eyebrow.Text = "LORA  /  WARNING" if warn_icon else "LORA  /  NOTIFICATION"
    eyebrow.Foreground = SolidColorBrush(Color.FromRgb(255,126,160) if warn_icon else Color.FromRgb(177,140,255))
    eyebrow.FontSize = 10
    eyebrow.Margin = Thickness(0,0,0,8)
    root.Children.Add(eyebrow)
    body = TextBlock()
    body.Text = str(message)
    body.TextWrapping = TextWrapping.Wrap
    body.Foreground = SolidColorBrush(Color.FromRgb(235,239,250))
    body.FontSize = 13
    body.Margin = Thickness(0,0,0,20)
    root.Children.Add(body)
    btn = Button()
    btn.Content = "OK"
    btn.Width = 100
    btn.Height = 34
    btn.HorizontalAlignment = HorizontalAlignment.Right
    btn.Background = SolidColorBrush(Color.FromRgb(128,87,232))
    btn.Foreground = SolidColorBrush(Color.FromRgb(255,255,255))
    btn.BorderBrush = SolidColorBrush(Color.FromRgb(167,131,255))
    btn.Click += lambda sender, args: win.Close()
    root.Children.Add(btn)
    win.Content = root
    win.ShowDialog()
    if exitscript:
        from pyrevit import script
        script.exit()
    return True


def lora_confirm(message, title="Lora", yes_text="Confirm", no_text="Cancel", **kwargs):
    """Themed confirmation dialog with context-appropriate button labels."""
    from System.Windows import Window, Thickness, SizeToContent, WindowStartupLocation, ResizeMode, HorizontalAlignment
    from System.Windows.Controls import StackPanel, TextBlock, Button, Grid, ColumnDefinition
    from System.Windows.Media import SolidColorBrush, Color
    win = Window()
    win.Title = title
    win.Width = 520
    win.SizeToContent = SizeToContent.Height
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen
    win.ResizeMode = ResizeMode.NoResize
    win.Background = SolidColorBrush(Color.FromRgb(21,28,45))
    win.Foreground = SolidColorBrush(Color.FromRgb(243,245,255))
    root = StackPanel()
    root.Margin = Thickness(22)
    head = TextBlock()
    head.Text = "LORA  /  CONFIRM ACTION"
    head.Foreground = SolidColorBrush(Color.FromRgb(177,140,255))
    head.FontSize = 10
    head.Margin = Thickness(0,0,0,10)
    root.Children.Add(head)
    body = TextBlock()
    body.Text = str(message)
    body.TextWrapping = TextWrapping.Wrap
    body.Margin = Thickness(0,0,0,20)
    root.Children.Add(body)
    buttons = StackPanel()
    buttons.Orientation = 0
    buttons.HorizontalAlignment = HorizontalAlignment.Right
    result = {"yes": False}
    no = Button()
    no.Content = no_text
    no.Width = 100
    no.Height = 34
    no.Margin = Thickness(0,0,8,0)
    yes = Button()
    yes.Content = yes_text
    yes.Width = 120
    yes.Height = 34
    yes.Background = SolidColorBrush(Color.FromRgb(128,87,232))
    yes.Foreground = SolidColorBrush(Color.FromRgb(255,255,255))
    yes.Click += lambda sender, args: (result.update({"yes": True}), win.Close())
    no.Click += lambda sender, args: win.Close()
    buttons.Children.Add(no)
    buttons.Children.Add(yes)
    root.Children.Add(buttons)
    win.Content = root
    win.ShowDialog()
    return result["yes"]


def lora_apply_winforms_theme(form):
    """Apply Lora's dark navy/violet theme to a WinForms form and its children.

    Intended for compact pyRevit dialogs that use System.Windows.Forms. File
    pickers remain native Windows dialogs for usability and OS accessibility.
    Compatible with IronPython 2.7 and CPython-based pyRevit engines.
    """
    try:
        import System
        from System.Windows.Forms import (Form, Label, Button, ComboBox, TextBox,
            CheckBox, RadioButton, GroupBox, Panel, ListBox, DataGridView,
            FlatStyle, BorderStyle)
        palette = {
            "background": System.Drawing.Color.FromArgb(21, 28, 45),
            "surface": System.Drawing.Color.FromArgb(32, 42, 64),
            "surface_alt": System.Drawing.Color.FromArgb(27, 36, 55),
            "text": System.Drawing.Color.FromArgb(243, 245, 255),
            "muted": System.Drawing.Color.FromArgb(165, 176, 204),
            "accent": System.Drawing.Color.FromArgb(128, 87, 232),
            "border": System.Drawing.Color.FromArgb(89, 101, 132),
        }
        form.BackColor = palette["background"]
        form.ForeColor = palette["text"]
        try:
            form.Font = System.Drawing.Font("Segoe UI", 9.0)
        except Exception:
            pass

        def apply(control):
            try:
                if isinstance(control, (Form, Panel, GroupBox)):
                    control.BackColor = palette["background"]
                    control.ForeColor = palette["text"]
                elif isinstance(control, Label):
                    control.BackColor = System.Drawing.Color.Transparent
                    control.ForeColor = palette["text"]
                elif isinstance(control, Button):
                    control.BackColor = palette["accent"] if str(control.Text).lower() not in ("cancel", "close", "back") else palette["surface"]
                    control.ForeColor = palette["text"]
                    control.FlatStyle = FlatStyle.Flat
                    control.FlatAppearance.BorderColor = palette["border"]
                    control.FlatAppearance.BorderSize = 1
                    control.UseVisualStyleBackColor = False
                elif isinstance(control, (ComboBox, TextBox, ListBox, DataGridView)):
                    control.BackColor = palette["surface"]
                    control.ForeColor = palette["text"]
                    if isinstance(control, TextBox):
                        control.BorderStyle = BorderStyle.FixedSingle
                elif isinstance(control, (CheckBox, RadioButton)):
                    control.BackColor = palette["background"]
                    control.ForeColor = palette["text"]
                else:
                    try:
                        control.ForeColor = palette["text"]
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                for child in control.Controls:
                    apply(child)
            except Exception:
                pass
        for control in form.Controls:
            apply(control)
        return form
    except Exception:
        # The theme must never prevent the actual BIM command from running.
        return form
