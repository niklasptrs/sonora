"""Transparent companion windows for Sonora's desktop scene."""
import math
import cairo
import gi
gi.require_version('Gtk','3.0')
gi.require_version('Gdk','3.0')
gi.require_version('PangoCairo','1.0')
from gi.repository import Gdk, Gtk, GLib, Pango, PangoCairo, GdkPixbuf
from desktop_widget import GtkLayerShell


def rounded(cr, x, y, w, h, radius):
    cr.new_sub_path()
    for px, py, angle in ((x+w-radius,y+radius,-90),(x+w-radius,y+h-radius,0),
                          (x+radius,y+h-radius,90),(x+radius,y+radius,180)):
        cr.arc(px, py, radius, math.radians(angle), math.radians(angle+90))
    cr.close_path()


def artwork(cr, image, x, y, size, colour):
    cr.save()
    rounded(cr,x,y,size,size,size*.15)
    cr.clip()
    cr.set_source_rgba(*colour,.36)
    cr.paint()
    if image:
        factor = max(size/image.get_width(), size/image.get_height())
        cr.translate(x+(size-image.get_width()*factor)/2, y+(size-image.get_height()*factor)/2)
        cr.scale(factor,factor)
        Gdk.cairo_set_source_pixbuf(cr,image,0,0)
        cr.paint()
    cr.restore()


class SceneWindow(Gtk.Window):
    def __init__(self, app, kind, monitor):
        super().__init__(title='Sonora · '+kind)
        self.app, self.kind, self.monitor = app,kind,monitor
        self.render_state = None
        self.drag = None
        self.edge = kind in ('top','bottom','left','right')
        self.layer = bool(GtkLayerShell and 'Wayland' in type(self.get_display()).__name__
                          and GtkLayerShell.is_supported())
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_pager_hint(True)
        self.set_skip_taskbar_hint(True)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.area = Gtk.DrawingArea()
        self.area.connect('draw',self.draw)
        self.add(self.area)
        self.get_style_context().add_class('desktop-ring')
        rect = monitor.get_geometry()
        if self.edge:
            horizontal = kind in ('top','bottom')
            size = (rect.width,12) if horizontal else (12,rect.height-24)
            self.x = rect.width-size[0] if kind=='right' else 0
            self.y = rect.height-size[1] if kind=='bottom' else (0 if horizontal else 12)
        else:
            size = (min(rect.width,app.settings['lyrics_width']),150) if kind=='lyrics' else (308,100)
            self.x,self.y = (app.settings[kind+'_x'],app.settings[kind+'_y'])
            if (self.x,self.y)==(-1,-1):
                self.x = int(rect.width*(.09 if kind=='lyrics' else .035))
                self.y = int(rect.height*(.34 if kind=='lyrics' else .67))
        self.area.set_size_request(*map(int,size))
        if self.layer:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self,'sonora-'+kind)
            GtkLayerShell.set_keyboard_mode(self,GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_monitor(self,monitor)
            for edge in (GtkLayerShell.Edge.TOP,GtkLayerShell.Edge.LEFT):
                GtkLayerShell.set_anchor(self,edge,True)
        self.update_layer()
        self.place(self.x,self.y)
        if self.edge:
            self.connect('realize',lambda *_: self.get_window().input_shape_combine_region(cairo.Region(),0,0))
        else:
            self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                            Gdk.EventMask.POINTER_MOTION_MASK)
            self.connect('button-press-event',self.press)
            self.connect('motion-notify-event',self.motion)
            self.connect('button-release-event',self.release)
        self.show_all()

    def update_layer(self):
        desktop = self.app.settings['widget_placement']=='Desktop'
        if self.layer:
            GtkLayerShell.set_layer(self,GtkLayerShell.Layer.BOTTOM if desktop else GtkLayerShell.Layer.TOP)
        else:
            self.set_type_hint(Gdk.WindowTypeHint.DESKTOP if desktop else Gdk.WindowTypeHint.UTILITY)
            self.set_keep_above(not desktop)
            self.set_keep_below(desktop)

    def place(self,x,y):
        rect=self.monitor.get_geometry()
        self.x=max(0,min(int(x),rect.width-(1 if self.edge else 50)))
        self.y=max(0,min(int(y),rect.height-(1 if self.edge else 36)))
        if self.layer:
            GtkLayerShell.set_margin(self,GtkLayerShell.Edge.LEFT,self.x)
            GtkLayerShell.set_margin(self,GtkLayerShell.Edge.TOP,self.y)
        else:
            self.move(rect.x+self.x,rect.y+self.y)

    def press(self,_,event):
        if event.button==1:
            self.drag=(event.x,event.y)
            return True
        return False

    def motion(self,_,event):
        if self.drag and event.state & Gdk.ModifierType.BUTTON1_MASK:
            self.place(self.x+event.x-self.drag[0],self.y+event.y-self.drag[1])
            return True
        return False

    def release(self,_,event):
        if self.drag and event.button==1:
            self.drag=None
            self.app.settings.update({self.kind+'_x':self.x,self.kind+'_y':self.y})
            self.app.save()
            return True
        return False

    def label(self,cr,text,x,y,width,size,colour,bold=False,align='Links',lines=1,highlight=None):
        layout=self.area.create_pango_layout(text)
        font=Pango.FontDescription('Sans Bold' if bold else 'Sans')
        font.set_absolute_size(size*Pango.SCALE)
        layout.set_font_description(font)
        layout.set_width(round(width*Pango.SCALE))
        layout.set_height(-lines)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_alignment({'Links':Pango.Alignment.LEFT,'Mitte':Pango.Alignment.CENTER,
                              'Rechts':Pango.Alignment.RIGHT}[align])
        if highlight and highlight[0]:
            attr=Pango.attr_foreground_new(*(round(channel*65535) for channel in highlight[1]))
            attr.end_index=min(highlight[0],len(text.encode()))
            attrs=Pango.AttrList()
            attrs.insert(attr)
            layout.set_attributes(attrs)
        cr.move_to(x,y)
        cr.set_source_rgba(*colour)
        PangoCairo.show_layout(cr,layout)

    def draw(self,area,cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0,0,0,0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        w,h=area.get_allocated_width(),area.get_allocated_height()
        palette=self.app.scene_palette()
        if self.edge:
            endpoints={'top':(0,0,0,h),'bottom':(0,h,0,0),'left':(0,0,w,0),'right':(w,0,0,0)}
            field=cairo.LinearGradient(*endpoints[self.kind])
            field.add_color_stop_rgba(0,*palette[0],.23+.45*self.app.scene_bass)
            field.add_color_stop_rgba(.35,*palette[1],.12+.20*self.app.scene_bass)
            field.add_color_stop_rgba(1,*palette[1],0)
            cr.set_source(field)
            cr.paint()
            return
        media=self.app.current_media()
        if self.kind=='lyrics':
            frame=self.app.lyrics_frame()
            text=frame.text if self.app.lyrics_data and self.app.lyrics_data.lines else media.title
            size=self.app.settings['lyrics_font_size']
            position=self.app.track_position()[0]+round(self.app.settings['lyrics_offset']*1000)
            line=self.app.lyrics_data.line_at(position) if self.app.lyrics_data else None
            active=line.highlighted_bytes(position) if line else 0
            for offset,colour in ((2,(0,0,0,.8)),(0,(.98,.98,.98,1))):
                self.label(cr,text,8+frame.dx+offset,8+frame.dy+offset,w-16,size,
                           (*colour[:3],colour[3]*frame.opacity),True,self.app.settings['lyrics_align'],3,(active,palette[0]) if not offset else None)
        else:
            rounded(cr,1,1,w-2,h-2,18)
            cr.set_source_rgba(.045,.048,.063,.92)
            cr.fill_preserve()
            cr.set_source_rgba(*palette[0],.45)
            cr.stroke()
            artwork(cr,self.app.cover,14,14,68,palette[0])
            self.label(cr,self.app.ui.text('JETZT LÄUFT'),94,14,w-108,10,(*palette[2],1),True)
            self.label(cr,media.title,94,32,w-108,17,(1,1,1,1),True)
            self.label(cr,media.artist,94,58,w-108,12,(.79,.8,.84,1))
            for i,(title,artist,url) in enumerate(self.app.upcoming):
                y=96+i*44
                artwork(cr,self.app.queue_images.get(url),16,y,34,palette[1])
                self.label(cr,title,62,y,w-76,13,(.95,.95,.98,1),True)
                self.label(cr,artist,62,y+18,w-76,11,(.7,.72,.78,1))
