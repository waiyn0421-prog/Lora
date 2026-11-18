# -*- coding: utf-8 -*-


# Lora shared extension library (single source of truth)
import os as _lora_os, sys as _lora_sys
_lora_lib = _lora_os.path.abspath(_lora_os.path.join(_lora_os.path.dirname(__file__), "..", "..", "..", "lib"))
if _lora_lib not in _lora_sys.path:
    _lora_sys.path.insert(0, _lora_lib)
from LoraDialogTheme import lora_select_from_list, lora_checklist_from_list, lora_alert, lora_confirm
from LoraCore import element_id_value, ids_equal, safe_text

"""Lora Parameter Generator for Revit 2023–2026 (runtime validation required).
Creates Lora shared parameter definitions and binds selected parameters to selected categories.
Run inside pyRevit / IronPython-compatible environment.
"""
from pyrevit import revit, DB, forms, script
import os
import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
from System.Windows.Markup import XamlReader

doc = revit.doc
app = __revit__.Application
logger = script.get_logger()

PARAMS = [
    ("Lora_Service_Abbreviation", "Text", "Identity Data", "Service abbreviation read from MEP system"),
    ("Lora_Service_Size", "Text", "Identity Data", "Formatted service size, e.g. Dia 200 or 600 x 300"),
    ("Lora_Elevation_Type", "Text", "Identity Data", "Automatically assigned display reference: BOP/BOD/BOT"),
    ("Lora_Reference_FFL_Name", "Text", "Constraints", "Name of the local floor reference"),
    ("Lora_Reference_FFL_Elevation", "Length", "Constraints", "Absolute local floor elevation"),
    ("Lora_Service_Elevation", "Length", "Constraints", "Absolute service surface elevation used for display"),
    ("Lora_Relative_Elevation", "Length", "Constraints", "Selected relative service elevation"),
    ("Lora_Relative_Bottom_Elevation", "Length", "Constraints", "Bottom surface elevation relative to local floor"),
    ("Lora_Relative_Top_Elevation", "Length", "Constraints", "Top surface elevation relative to local floor"),
    ("Lora_Elevation_Status", "Text", "Identity Data", "Updater result or review status"),
]

CATEGORY_NAMES = [
    "Pipes", "Pipe Fittings", "Pipe Accessories",
    "Ducts", "Duct Fittings", "Duct Accessories",
    "Cable Trays", "Cable Tray Fittings",
    "Conduits", "Conduit Fittings",
]

GROUPS = [
    "Identity Data", "Constraints", "Dimensions", "Data", "Text",
    "Geometry", "Graphics", "Materials and Finishes", "Phasing",
]

def get_category_by_name(name):
    try:
        return doc.Settings.Categories.get_Item(name)
    except Exception:
        pass
    # Localized/alternate names: resolve through BuiltInCategory.
    map_names = {
        "Pipes": DB.BuiltInCategory.OST_PipeCurves,
        "Pipe Fittings": DB.BuiltInCategory.OST_PipeFitting,
        "Pipe Accessories": DB.BuiltInCategory.OST_PipeAccessory,
        "Ducts": DB.BuiltInCategory.OST_DuctCurves,
        "Duct Fittings": DB.BuiltInCategory.OST_DuctFitting,
        "Duct Accessories": DB.BuiltInCategory.OST_DuctAccessory,
        "Cable Trays": DB.BuiltInCategory.OST_CableTray,
        "Cable Tray Fittings": DB.BuiltInCategory.OST_CableTrayFitting,
        "Conduits": DB.BuiltInCategory.OST_Conduit,
        "Conduit Fittings": DB.BuiltInCategory.OST_ConduitFitting,
    }
    bic = map_names.get(name)
    if bic is not None:
        try:
            return doc.Settings.Categories.get_Item(bic)
        except Exception:
            return None
    return None

def spec_for(kind):
    if kind == "Length":
        try:
            return DB.SpecTypeId.Length
        except Exception:
            return DB.ParameterType.Length
    try:
        return DB.SpecTypeId.String.Text
    except Exception:
        return DB.ParameterType.Text

def group_type_id(group_name):
    # Revit 2025+ API uses ForgeTypeId groups.
    try:
        return getattr(DB.GroupTypeId, group_name.replace(" ", ""))
    except Exception:
        legacy = {
            "Identity Data": DB.BuiltInParameterGroup.PG_IDENTITY_DATA,
            "Constraints": DB.BuiltInParameterGroup.PG_CONSTRAINTS,
            "Dimensions": DB.BuiltInParameterGroup.PG_GEOMETRY,
            "Data": DB.BuiltInParameterGroup.PG_DATA,
            "Text": DB.BuiltInParameterGroup.PG_TEXT,
            "Geometry": DB.BuiltInParameterGroup.PG_GEOMETRY,
            "Graphics": DB.BuiltInParameterGroup.PG_GRAPHICS,
            "Materials and Finishes": DB.BuiltInParameterGroup.PG_MATERIALS,
            "Phasing": DB.BuiltInParameterGroup.PG_PHASING,
        }
        return legacy.get(group_name, DB.BuiltInParameterGroup.PG_IDENTITY_DATA)

def choose_shared_parameter_file():
    current = app.SharedParametersFilename
    options = ["Use current shared parameter file", "Create/select a Lora shared parameter file"]
    choice = lora_select_from_list(options, title="Lora Parameter Generator — Shared Parameter File",
                                       button_name="Continue", multiselect=False)
    if not choice:
        return None
    if choice == options[0]:
        if current and os.path.exists(current):
            return current
        lora_alert(
            "No existing shared parameter file is configured in Revit.\n\n"
            "Choose 'Create/select a Lora shared parameter file' to create one.",
            title="Lora Parameter Generator")
        return None
    path = forms.save_file(file_ext="txt", default_name="Lora_SharedParameters.txt",
                           title="Create/select shared parameter file")
    if not path:
        return None
    if not os.path.exists(path):
        try:
            with open(path, "w") as f:
                f.write("# This is a Revit shared parameter file.\n")
                f.write("# Do not edit manually.\n")
                f.write("*META\tVERSION\tMINVERSION\n")
                f.write("META\t2\t1\n")
                f.write("*GROUP\tID\tNAME\n")
                f.write("*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n")
        except Exception as ex:
            lora_alert("Could not create shared parameter file:\n{}".format(ex), exitscript=True)
    app.SharedParametersFilename = path
    return path

def ensure_group(spfile, group_name):
    group = spfile.Groups.get_Item(group_name)
    if group is None:
        group = spfile.Groups.Create(group_name)
    return group

def get_or_create_definition(group, name, kind, description):
    definition = group.Definitions.get_Item(name)
    if definition:
        return definition
    options = DB.ExternalDefinitionCreationOptions(name, spec_for(kind))
    options.Visible = True
    try:
        options.Description = description
    except Exception:
        pass
    return group.Definitions.Create(options)

def run_parameter_generator():
    if doc.IsReadOnly:
        lora_alert("The active project is read-only. Open an editable project and try again.", exitscript=True)

    file_path = choose_shared_parameter_file()
    if not file_path:
        return
    spfile = app.OpenSharedParameterFile()
    if spfile is None:
        lora_alert("Revit could not open the shared parameter file:\n{}".format(file_path), exitscript=True)

    param_names = [p[0] for p in PARAMS]
    selected_params = lora_checklist_from_list(
        param_names, title="Select Lora parameters to generate",
        button_name="Next", width=520, height=470, check_all=True)
    if not selected_params:
        return

    categories = [c for c in CATEGORY_NAMES if get_category_by_name(c) is not None]
    selected_categories = lora_checklist_from_list(
        categories, title="Assign project parameters to categories",
        button_name="Next", width=520, height=470, check_all=True)
    if not selected_categories:
        return

    group_choice = lora_select_from_list(
        GROUPS, title="Choose project parameter group",
        button_name="Create and Assign", multiselect=False)
    if not group_choice:
        return

    catset = app.Create.NewCategorySet()
    for name in selected_categories:
        cat = get_category_by_name(name)
        if cat:
            catset.Insert(cat)
    if catset.Size == 0:
        lora_alert("No valid categories were selected.", exitscript=True)

    binding = app.Create.NewInstanceBinding(catset)
    pgroup = ensure_group(spfile, "MEP Tagging parameter")
    created = []
    skipped = []
    errors = []

    t = DB.Transaction(doc, "Lora — Generate and Bind Shared Parameters")
    t.Start()
    try:
        for name, kind, default_group, description in PARAMS:
            if name not in selected_params:
                continue
            try:
                definition = get_or_create_definition(pgroup, name, kind, description)
                # If already bound, ReInsert updates category binding and parameter group.
                existing = doc.ParameterBindings.get_Item(definition)
                if existing:
                    ok = doc.ParameterBindings.ReInsert(definition, binding, group_type_id(group_choice))
                else:
                    ok = doc.ParameterBindings.Insert(definition, binding, group_type_id(group_choice))
                if ok:
                    created.append(name)
                else:
                    skipped.append(name + " (binding not changed)")
            except Exception as ex:
                errors.append("{}: {}".format(name, ex))
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    summary = "Lora Parameter Generator finished.\n\nBound: {}\nNot changed: {}\nErrors: {}".format(
        len(created), len(skipped), len(errors))
    if created:
        summary += "\n\nBound parameters:\n" + "\n".join(created)
    if skipped:
        summary += "\n\nNot changed:\n" + "\n".join(skipped)
    if errors:
        summary += "\n\nErrors:\n" + "\n".join(errors)
    lora_alert(summary, title="Lora Parameter Generator", warn_icon=bool(errors))

def run_tag_generator():
    # Check whether the expected Lora parameters are present in the active project.
    missing = []
    for pname, kind, default_group, description in PARAMS:
        try:
            found = False
            iterator = doc.ParameterBindings.ForwardIterator()
            iterator.Reset()
            while iterator.MoveNext():
                definition = iterator.Key
                if definition and definition.Name == pname:
                    found = True
                    break
            if not found:
                missing.append(pname)
        except Exception:
            missing.append(pname)
    if missing:
        choice = lora_confirm(
            "Some Lora project parameters are not bound yet ({} missing).\n\n"
            "Run Parameter Generator now?\n\n"
            "Missing: {}".format(len(missing), ", ".join(missing[:6])),
            title="Lora Tag Generator", yes_text="Open Parameter Generator",
            no_text="Cancel")
        if choice:
            run_parameter_generator()
        return
    lora_alert(
        "Lora parameters are ready to use.\\n\\n"
        "Tag-family creation still requires a compatible Revit tag-family template and "
        "family-document workflow. This build combines the entry point and parameter setup, "
        "but does not yet automatically author and load a new .RFA tag family.\\n\\n"
        "Next: use a compatible tag family template, then map the Lora shared parameters "
        "to its label and load it into the project.",
        title="Lora Tag Generator")

def main():
    xaml = r"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Lora Tag Creation" Height="340" Width="520"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#151C2D" Foreground="#F3F5FF" FontFamily="Segoe UI"
        FontSize="13" ShowInTaskbar="True">
  <Window.Resources>
    <Style x:Key="Choice" TargetType="{x:Type RadioButton}">
      <Setter Property="Foreground" Value="#F3F5FF"/>
      <Setter Property="Background" Value="#202A40"/>
      <Setter Property="BorderBrush" Value="#45516F"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="Padding" Value="14,12"/>
      <Setter Property="Margin" Value="0,0,0,10"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="{x:Type RadioButton}">
            <Border x:Name="Card" Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}"
                    CornerRadius="10" Padding="{TemplateBinding Padding}">
              <Grid>
                <Grid.ColumnDefinitions><ColumnDefinition Width="32"/><ColumnDefinition Width="*"/><ColumnDefinition Width="22"/></Grid.ColumnDefinitions>
                <Border Width="22" Height="22" CornerRadius="7" Background="#313B55"
                        BorderBrush="#8F68FF" BorderThickness="1" VerticalAlignment="Center">
                  <TextBlock Text="✦" Foreground="#B18CFF" FontSize="14"
                             HorizontalAlignment="Center" VerticalAlignment="Center"/>
                </Border>
                <ContentPresenter Grid.Column="1" VerticalAlignment="Center"/>
                <TextBlock x:Name="Check" Grid.Column="2" Text="✓" FontSize="18"
                           Foreground="#B18CFF" VerticalAlignment="Center"
                           HorizontalAlignment="Center" Visibility="Collapsed"/>
              </Grid>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Card" Property="Background" Value="#2A3550"/>
                <Setter TargetName="Card" Property="BorderBrush" Value="#A783FF"/>
              </Trigger>
              <Trigger Property="IsChecked" Value="True">
                <Setter TargetName="Card" Property="Background" Value="#30294F"/>
                <Setter TargetName="Card" Property="BorderBrush" Value="#A783FF"/>
                <Setter TargetName="Check" Property="Visibility" Value="Visible"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Action" TargetType="{x:Type Button}">
      <Setter Property="Foreground" Value="White"/><Setter Property="Background" Value="#8057E8"/>
      <Setter Property="BorderBrush" Value="#A783FF"/><Setter Property="BorderThickness" Value="1"/>
      <Setter Property="FontSize" Value="14"/><Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Padding" Value="15,9"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="{x:Type Button}">
            <Border x:Name="B" CornerRadius="8" Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="B" Property="Background" Value="#956FFF"/></Trigger>
              <Trigger Property="IsPressed" Value="True"><Setter TargetName="B" Property="Background" Value="#6540C7"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>
  <Grid ClipToBounds="True">
    <Canvas IsHitTestVisible="False">
      <Ellipse x:Name="GlowOne" Width="250" Height="250" Canvas.Left="310" Canvas.Top="-110" Fill="#713FE0" Opacity="0.10"/>
      <Ellipse x:Name="GlowTwo" Width="210" Height="210" Canvas.Left="-100" Canvas.Top="190" Fill="#E94F9B" Opacity="0.06"/>
    </Canvas>
    <Grid Margin="24,20,24,20">
      <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <StackPanel Grid.Row="0" Margin="0,0,0,16">
        <TextBlock Text="LORA  /  MEP TOOLS" Foreground="#B18CFF" FontSize="10" FontWeight="Bold"/>
        <TextBlock Text="Tag Creation" FontSize="25" FontWeight="SemiBold" Foreground="#F6F7FF" Margin="0,4,0,3"/>
        <TextBlock Text="Choose how you want to set up your MEP tags." Foreground="#AEB8D0" FontSize="12"/>
      </StackPanel>
      <StackPanel Grid.Row="1" VerticalAlignment="Center">
        <RadioButton x:Name="ParameterOption" GroupName="TagMode" Style="{StaticResource Choice}" IsChecked="True">
          <StackPanel>
            <TextBlock Text="Parameter Generator" FontWeight="SemiBold" FontSize="14"/>
            <TextBlock Text="Create shared parameters and bind them to project categories." Foreground="#AEB8D0" FontSize="11" Margin="0,3,0,0"/>
          </StackPanel>
        </RadioButton>
        <RadioButton x:Name="TagOption" GroupName="TagMode" Style="{StaticResource Choice}">
          <StackPanel>
            <TextBlock Text="Tag Generator" FontWeight="SemiBold" FontSize="14"/>
            <TextBlock Text="Check parameter readiness and continue to tagging setup." Foreground="#AEB8D0" FontSize="11" Margin="0,3,0,0"/>
          </StackPanel>
        </RadioButton>
      </StackPanel>
      <Grid Grid.Row="2" Margin="0,10,0,0">
        <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="125"/></Grid.ColumnDefinitions>
        <TextBlock Text="LORA  •  DESIGN / BIM" Foreground="#7886A5" FontSize="10" VerticalAlignment="Center"/>
        <Button x:Name="ContinueButton" Grid.Column="1" Content="Continue  →" Style="{StaticResource Action}"/>
      </Grid>
    </Grid>
    <Rectangle Height="3" VerticalAlignment="Top" Fill="#8D5BFF" Opacity="0.9"/>
    <Rectangle Height="1" VerticalAlignment="Bottom" Fill="#43506D" Opacity="0.7"/>
  </Grid>
</Window>
"""
    try:
        window = XamlReader.Parse(xaml)
    except Exception as ex:
        lora_alert("Could not open the Lora Tag Creation dialog:\n{}".format(ex),
                    title="Lora Tag Creation")
        return

    # Subtle animated violet/pink background glow; gracefully degrades if animation is unsupported.
    try:
        from System.Windows.Media.Animation import DoubleAnimation, RepeatBehavior
        from System import TimeSpan
        glow1 = window.FindName("GlowOne")
        glow2 = window.FindName("GlowTwo")
        anim1 = DoubleAnimation()
        anim1.From = 0.06
        anim1.To = 0.22
        anim1.Duration = TimeSpan.FromSeconds(3.8)
        anim1.AutoReverse = True
        anim1.RepeatBehavior = RepeatBehavior.Forever
        glow1.BeginAnimation(type(glow1).OpacityProperty, anim1)
        anim2 = DoubleAnimation()
        anim2.From = 0.03
        anim2.To = 0.13
        anim2.Duration = TimeSpan.FromSeconds(5.1)
        anim2.AutoReverse = True
        anim2.RepeatBehavior = RepeatBehavior.Forever
        glow2.BeginAnimation(type(glow2).OpacityProperty, anim2)
    except Exception as ex:
        logger.warning("Dialog background animation unavailable: {}".format(ex))

    result = {"choice": None}
    def on_continue(sender, args):
        result["choice"] = "Tag Generator" if window.FindName("TagOption").IsChecked else "Parameter Generator"
        window.DialogResult = True
        window.Close()
    window.FindName("ContinueButton").Click += on_continue
    window.ShowDialog()
    choice = result["choice"]
    if not choice:
        return
    try:
        if choice == "Parameter Generator":
            run_parameter_generator()
        else:
            run_tag_generator()
    except Exception as ex:
        logger.exception("Lora Parameter/Tag Generator failed")
        lora_alert(
            "Lora could not complete the selected operation.\n\n"
            "Error: {}\n\n"
            "Open the pyRevit output panel for the full traceback."
            .format(ex),
            title="Lora Parameter Generator Error", warn_icon=True)

if __name__ == "__main__":
    main()
