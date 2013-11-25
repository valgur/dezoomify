#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""

This is a module containing the class description of the TiledImagePanel
class, which is a wxPython widget to hold a scrollable image composed of 
many individual tiles.

"""


import wx
import random
import threading

try:
    import Image
    import ImageDraw
except ImportError:
    print('(ERR) Needs PIL to run. You can get PIL at http://www.pythonware.com/products/pil/. Exiting.')
    sys.exit()
    
import imageConverter

EVT_ADD_TILE = 0

class BufferedScrolledWindow(wx.ScrolledWindow):

    """

    A Buffered window class.

    To use it, subclass it and define a Draw(DC) method that takes a DC
    to draw to. In that method, put the code needed to draw the picture
    you want. The window will automatically be double buffered, and the
    screen will be automatically updated when a Paint event is received.

    When the drawing needs to change, you app needs to call the
    UpdateDrawing() method. Since the drawing is stored in a bitmap, you
    can also save the drawing to file by calling the
    SaveToFile(self, file_name, file_type) method.

    """
    def __init__(self, *args, **kwargs):
        # make sure the NO_FULL_REPAINT_ON_RESIZE style flag is set.
        kwargs['style'] = kwargs.setdefault('style', wx.NO_FULL_REPAINT_ON_RESIZE) | wx.NO_FULL_REPAINT_ON_RESIZE
        super(BufferedScrolledWindow, self).__init__(*args, **kwargs)
        
        self.scrollPos = [0,0]

        wx.EVT_PAINT(self, self.OnPaint)
        wx.EVT_SIZE(self, self.OnSize)
        wx.EVT_SCROLLWIN(self, self.OnScroll)
        
        # OnSize called to make sure the buffer is initialized.
        # This might result in OnSize getting called twice on some
        # platforms at initialization, but little harm done.
        self.OnSize(None)

    def _Draw(self, dc):
        ## just here as a place holder.
        ## This method should be over-ridden when subclassed
        pass

    def OnPaint(self, event):
        # All that is needed here is to draw the buffer to screen

        dc = wx.BufferedPaintDC(self, self._Buffer)

    def OnSize(self,event):
        # The Buffer init is done here, to make sure the buffer is always
        # the same size as the Window
        #Size  = self.GetClientSizeTuple()
        Size  = self.ClientSize

        # Make new offscreen bitmap: this bitmap will always have the
        # current drawing in it, so it can be used to save the image to
        # a file, or whatever.
        self._Buffer = wx.EmptyBitmap(*Size)
        self.UpdateDrawing()
        
    def OnScroll(self, event):
        
        self.scrollPos[0 if event.GetOrientation()==wx.HORIZONTAL else 1] = event.GetPosition()
        self.Scroll(self.scrollPos[0],self.scrollPos[1])
        self.UpdateDrawing()
        
    def SaveToFile(self, FileName, FileType=wx.BITMAP_TYPE_PNG):
        ## This will save the contents of the buffer
        ## to the specified file. See the wxWindows docs for 
        ## wx.Bitmap::SaveFile for the details
        self._Buffer.SaveFile(FileName, FileType)

    def UpdateDrawing(self):
        """
        This would get called if the drawing needed to change, for whatever reason.

        The idea here is that the drawing is based on some data generated
        elsewhere in the system. If that data changes, the drawing needs to
        be updated.

        This code re-draws the buffer, then calls Update, which forces a paint event.
        """
        dc = wx.MemoryDC()
        dc.SelectObject(self._Buffer)
        self._Draw(dc)
        del dc # need to get rid of the MemoryDC before Update() is called.
        self.Refresh()
        self.Update()
        


class TiledImagePanel(BufferedScrolledWindow):

    scrollUnit = 20
    scale = 1
    backgroundColour="#000000"
    highlightColour ="#FF0000"
    foregroundColour="#FFFFFF"
    saveQuality = 90
    overviewScale = 2
    overviewBorder= 5 #will be scaled
    showGrid = True
    tileDict = {}
    size = tileSize = None
    
    freezeUpdates = False
        
    def __init__(self, *args, **kwargs):
            
        super(TiledImagePanel, self).__init__(*args, **kwargs)
        
        #custom events
        EVT_RESULT(self, self.OnReturnEvent)

    def _Draw(self, dc):
        
        if self.freezeUpdates: return

        dc.SetBackground( wx.Brush(self.backgroundColour) )
        dc.Clear() # make sure you clear the bitmap!
        
        self.PrepareDC(dc)
        
        clientAreaSize = self.GetClientSizeTuple()
        scrollPos = self.GetViewStart()
        
        visibleRegion = { 'left': scrollPos[0]*self.scrollUnit,
                    'right':clientAreaSize[0] + scrollPos[0]*self.scrollUnit,
                    'top':scrollPos[1]*self.scrollUnit,
                    'bottom':clientAreaSize[1] + scrollPos[1]*self.scrollUnit}

        if not self.size: return #we haven't got far enough to display anything!
        
        
        if self.showGrid:
            x = y = 0
            lineList =[]
            while x < self.size[0]+1:
                lineList.append((x, 0, x, self.size[1]))
                x += self.tileSize[0]
                
            while y < self.size[1]+1:
                lineList.append((0, y, self.size[0], y))
                y += self.tileSize[1]
                
            dc.DrawLineList(lineList, pens=wx.Pen(self.highlightColour))
        
        for coord in self.tileDict:
            #only draw if it is visbile
            if (     (coord[0]  * self.tileSize[0] < visibleRegion['right']) #image is to the left of the right border
                and ((coord[0]+1) * self.tileSize[0] > visibleRegion['left']) #image is to the right of the left border
                and  (coord[1] * self.tileSize[1] < visibleRegion['bottom'])
                and ((coord[1]+1) * self.tileSize[1] > visibleRegion['top'])):
                
                dc.DrawBitmap(self.tileDict[coord], 
                                    coord[0]*self.tileSize[0], 
                                    coord[1]*self.tileSize[1])
                            
                            
        # Place the overview image
        overviewSize = self.overviewImage.size
        
        overviewBmp= imageConverter.WxBitmapFromPilImage(self.overviewImage)
        bitmapOffset = (    clientAreaSize[0]+scrollPos[0]*self.scrollUnit-overviewSize[0]-50,
                            clientAreaSize[1]+scrollPos[1]*self.scrollUnit-overviewSize[1]-50)

        dc.DrawBitmap(overviewBmp, 
                        bitmapOffset[0],
                        bitmapOffset[1])
                        
        overviewRectangle = (   bitmapOffset[0] + self.overviewBorder + (visibleRegion['left']   // (self.tileSize[0]//self.overviewScale)),
                                bitmapOffset[1] + self.overviewBorder + (visibleRegion['top']    // (self.tileSize[1]//self.overviewScale)),
                                (clientAreaSize[0]  // (self.tileSize[0]//self.overviewScale))+1,
                                (clientAreaSize[1] // (self.tileSize[1]//self.overviewScale))+1  )
                    
                        
        dc.DrawRectangleList( [overviewRectangle],
                        pens=wx.Pen(self.highlightColour),
                        brushes=wx.Brush(self.backgroundColour, style=wx.TRANSPARENT))



    def OnReturnEvent(self, event):
        """
        This is the handler for the custom events
        """
        pass
    
    #def 


    def _SetupImageOverview(self):
                
        width = (self.size[0]//self.tileSize[0])*self.overviewScale + self.overviewBorder*2
        height = (self.size[1]//self.tileSize[1])*self.overviewScale + self.overviewBorder*2
        self.overviewImage = Image.new('RGB', (width,height), self.highlightColour)
        
        self.overviewDraw = ImageDraw.Draw(self.overviewImage)
    
        self.overviewDraw.rectangle( [self.overviewBorder, self.overviewBorder, width-self.overviewBorder-1, height-self.overviewBorder-1],
                                        fill=self.backgroundColour)
                            
                            
                            
    def _ConstructFullImage(self):
        """
        Constructs a PIL image holding the full image.
        
        returns :   PIL image
        """
        
        try:
            fullImage = Image.new('RGB', (self.size[0], self.size[1]), self.backgroundColour)
        except MemoryError:
            print("ERR: Image too large to fit into memory. Exiting")
            sys.exit(2)
        
        for coord in self.tileDict:
            tileImage =   imageConverter.PilImageFromWxBitmap(self.tileDict[coord])
            fullImage.paste(tileImage, (coord[0]*self.tileSize[0], coord[1]*self.tileSize[1])) #paste into position
            
        return fullImage
                            
                            
    def SaveToFile(self, fileName):
        fullImage = self._ConstructFullImage()
        fullImage.save(fileName, quality=self.saveQuality ) #save the dezoomified file

        

    def ClearDrawing(self):
    
        self.tileDict = {}
        self._SetupImageOverview()
        self.UpdateDrawing()
        
                
    def AddTile(self, x, y, tile):
        """
        Externally-facing function to add a tile to the image matrix.
        
        x   : 0-indexed x-ordinate of the tile
        y   : 0-indexed y-ordinate
        tile: wx.Bitmap of the image to place
        """
        #wx.PostEvent(self, ReturnEvent(data={'x':x, 'y':y, 'tile':tile}, status=EVT_ADD_TILE))
        #tileQueue.append( (x, y, tile) )
        
        self._ProcessAddTile(x, y, tile)
        
    def _ProcessAddTile(self, x, y, tile):
        """
        Internal function to add a tile to the matrix. This directly
        edits the tile storage and the image, so it is NOT threadsafe,
        it is governed by a queue.
        """
        
        self.tileDict[(x,y)] = tile
        
        self.overviewDraw.rectangle( [x*self.overviewScale+self.overviewBorder, y*self.overviewScale+self.overviewBorder, 
                                        self.overviewBorder+(x+1)*self.overviewScale-1, self.overviewBorder+(y+1)*self.overviewScale-1],
                                        fill=self.foregroundColour)
        
        self.UpdateDrawing()
        
        
# Interface functions for setting and retrieving parameters
    def SetSize(self, xPixels, yPixels):
        """
        Set the size of the image area.
        """
        
        self.size = (xPixels, yPixels)

        self.SetScrollbars(self.scrollUnit, self.scrollUnit, 
                            xPixels//self.scrollUnit, 
                            yPixels//self.scrollUnit)
                            
        self._SetupImageOverview()
        
    def GetSize(self):
        return self.size

    
    def SetTileSize(self, tileSize):
        self.tileSize =  tileSize
        
    
    def GetTileSize(self):
        return self.tileSize
        
        
    def SetGrid(self, grid):
        self.showGrid = grid


    def GetGrid(self):
        return self.showGrid


    def SetSaveQuality(self, quality):
        self.saveQuality = quality


    def GetSaveQuality(self):
        return self.saveQuality

class TiledImagePanelExample(wx.Frame):
    
    rows = 10
    cols = 60
    tileSize = (128,128)
    
    def __init__(self):

        super(TiledImagePanelExample, self).__init__(parent=None,
                                                title='TileImagePanel tester', size=(800,600))
                                                    
        
        self.setup_widgets()
        
    def on_start(self, e):
        """Begin laying out tiles"""
        
        print("(INF) Laying out tiles now")
        
        self.startBtn.Enable(False)
        
        for i in range(self.rows*self.cols): #lay out 100 tiles randomly
        
            index = random.randint(0,self.rows*self.cols-1)
            y,x = divmod(index, self.cols)            
            tile = self.generate_tile()
            self.tip.AddTile(x,y,tile)
            
        self.tip.SaveToFile('/tmp/a.png')
            
        self.startBtn.Enable(True)
            
    
    def on_reset_image(self, e):

        self.tip.ClearDrawing()
        self.startBtn.Enable(True)
        
            
            
    def generate_tile(self):
        
        size = self.tip.GetTileSize()
        
        bmp = wx.EmptyBitmapRGBA(size[0], size[1], 
                                    red=random.randint(0,255),
                                    blue=random.randint(0,255), 
                                    green=random.randint(0,255),
                                    alpha=255)                                    
        return bmp
        
        
                                                    
    def setup_widgets(self):
        self.tip = TiledImagePanel(self)
        
        self.tip.SetTileSize(self.tileSize)
        self.tip.SetSize(self.cols * self.tileSize[0], self.rows * self.tileSize[1])
        
        self.startBtn = wx.Button(self, label="Populate with tiles")
        self.Bind( wx.EVT_BUTTON, self.on_start, self.startBtn)
        
        
        self.resetBtn = wx.Button(self, label="Reset image")
        self.Bind( wx.EVT_BUTTON, self.on_reset_image,  self.resetBtn)
        
        #lay out the widgets
        vBox = wx.BoxSizer(wx.VERTICAL)
        vBox.Add(self.tip, proportion=1, flag=wx.ALL|wx.EXPAND)
        
        hBox = wx.BoxSizer(wx.HORIZONTAL)
        hBox.Add(self.startBtn)
        hBox.Add(self.resetBtn)
        
        vBox.Add(hBox)
        
        self.SetSizer(vBox)
        
# THREAD HANDLING ==============================================================
#http://wiki.wxpython.org/LongRunningTasks

EVT_RESULT_ID = wx.NewId()

def EVT_RESULT(win, func):
    """Define Result Event."""
    win.Connect(-1, -1, EVT_RESULT_ID, func)

class ReturnEvent(wx.PyEvent):
    """Simple event to carry arbitrary result data."""
    def __init__(self, status, data):
        """Init Result Event."""
        wx.PyEvent.__init__(self)
        self.SetEventType(EVT_RESULT_ID)
        self.data = data
        self.status = status
        

if __name__ == "__main__":
    
    app = wx.App(False)  # Create a new app, don't redirect stdout/stderr to a window. #TODO fix this
    mainFrame = TiledImagePanelExample()
    mainFrame.Show()
    app.MainLoop()
