from pyModbusTCP.client import ModbusClient
from opcua import Server, ua, uamethod, Client
import time
from OurProductDataType_Lib import OurProduct
from datetime import datetime
import uuid
from client_server_classes import Input_Client, Machining_Client, Exit_Client, Storage_Client

##WORKFLOW DESCRIPTION
###
# For example a new piece is coming from the Input module:
# 1. After the program is launched (line 1539), all the objects are 
# initialized and the program enters the main loop line(1573).
# 2. Input passes a piece object by calling our OPCUA method (line 63) and this object is added to
# the (line 48) array
# 3. In the next iteration of main loop this object is added to main dictionary(line 46) such as
# the key is a piece object itself and the value is an appropriate Path object(for example PathM1 line 268)
# 4. In the next iteration the next step of the path is executed (line 1583 and line 275), after 
# the step is performed the step value is incremented to perform another step in the next loop iteration
# 5. For PathM1, after the sequence of steps the piece object is passed to Machining 1, then based on
# the current time the piece is going to the Storage or to the EXit. After that, the piece object is added
# to array(line 61) and in the next iteration should be deleted (line 1630)
####
# With above mentioned alogrithm it is possible to proccess multiple pieces at the same time, every pair
# of conveyor and switches is set free or busy (line 238) to prevent collisions and to process
# pieces one by one. Also through every main loop iteration the program is working with all available pieces.




###################################################
##DATA STRUCTURES
##these structures are used to peform thread-safe operations for
##working with pieces accessable from main thread as well as from the
##backround OPCUA thread

##main dictionary to store all pieces
piecesAtFlow = {}
##array for pieces coming from input to be added in the main dict
piecesToAddInput=[]
##array for pieces coming from storage to be added in the main dict
piecesToAddStorage=[]
##array for pieces to be deleted
piecesToDelete = []

###################################################
##OPC UA METHODS

## input asking if we are ready
@uamethod
def inputCheck(parent):
    if(checkNextCandS(conveyors["incU"], switches["insO"])):
        return True
    else:
        return False

## input gives us a piece object
@uamethod
def inputPass(parent, new_product):
    print(new_product)
    piecesToAddInput.append(new_product)
    
    return "OK"

## machining saying that a piece was recieved
@uamethod
def m1Received(parent, new_product):
    for p in piecesAtFlow:
        if (p.PartID == new_product.PartID):
            piecesAtFlow[p].stopConv = True
            minusPiece(p)
    return "OK"
## machining finished and asking if we are ready
@uamethod
def m1Check(parent):
    if(checkNextCandS(conveyors["incR"], switches["insK"])):
        return True
    else:
        return False
## machining gives us a piece
@uamethod
def m1Pass(parent, new_product):
    for p in piecesAtFlow:
        if (p.PartID == new_product.PartID):
            piecesAtFlow[p].processed = True
            plusPiece(p)
    return "OK"
## machining saying that a piece was recieved
@uamethod
def m2Received(parent, new_product):
    for p in piecesAtFlow:
        if (p.PartID == new_product.PartID):
            piecesAtFlow[p].stopConv = True
            minusPiece(p)
    return "OK"
## machining finished and asking if we are ready
@uamethod
def m2Check(parent):
    if(checkNextCandS(conveyors["incR"], switches["insS"])):
        return True
    else:
        return False
## machining gives us a piece
@uamethod
def m2Pass(parent, new_product):
    for p in piecesAtFlow:
        if (p.PartID == new_product.PartID):
            piecesAtFlow[p].processed = True
            plusPiece(p)
    return "OK"
## storage saying that the piece is received
@uamethod
def storageReceived(parent, new_product):
    print("received")
    for p in piecesAtFlow:
        if (p.PartID == new_product.PartID):
            piecesAtFlow[p].stopConv = True
    return "OK"
## storage asking if we are ready 
@uamethod
def storageCheck(parent):
    print("check")
    if(conveyors["excU"].isFree and switches["exsQ"].isFree):
        return True
    else:
        return False
## storage gives us a piece
@uamethod
def storagePass(parent, new_product):
    conveyors["excU"].isFree = False
    switches["exsQ"].isFree = False
    print(new_product)
    piecesToAddStorage.append(new_product)
    return "OK"
## exit saying that the piece is received
@uamethod
def exitReceived(parent, new_product):
    for p in piecesAtFlow:
        if (p.PartID == new_product.PartID):
            piecesAtFlow[p].stopConv = True
    return "OK"

#####################################################################
##Auxilary functions for path objects
## move conveyor
def startConveyor(conveyor,direction, write):
    if(direction == 0):
        write = writeBits(conveyor.regBack, write)
    elif(direction == 1):
        write = writeBits(conveyor.regForward, write)
    return write
## stop conveyor
def stopConveyor(conveyor, write):
    if(searchWriteBits(write, conveyor.regForward)):
        return clearBits(conveyor.regForward, write)
    elif(searchWriteBits(write, conveyor.regBack)):
        return clearBits(conveyor.regBack, write)
    else:
        return write
    
## move switch
def startSwitcher(switcher,pos, write):
    write = clearSwitcherBits(switcher, write)
    if(pos == 1):
        write = writeBits(switcher.regPos1, write)
    elif(pos == 2):
        write = writeBits(switcher.regPos2, write)
    elif(pos == 3):
        write = writeBits(switcher.regPos3, write)
    return write

## clear switcher bits
def clearSwitcherBits(switcher, write):
    if(searchWriteBits(write, switcher.regPos1)):
        return clearBits(switcher.regPos1, write)
    elif(searchWriteBits(write, switcher.regPos2)):
        return clearBits(switcher.regPos2, write)
    elif(searchWriteBits(write, switcher.regPos3)):
        return clearBits(switcher.regPos3, write)
    else:
        return write

## move the separator
def activateSeparator(sep, write):
    if(searchWriteBits(write, sep.regActuator)):
        return clearBits(sep.regActuator, write)
    else:
        return writeBits(sep.regActuator, write)

## check if a piece is inside the switch
def isPieceInSwitch(conveyorSensor,switcher,read):
    if(not readSensor(read, conveyorSensor) and readSensor(read, switcher.regIsPieceIn)):
        return True
    else:
        return False

## check if piece inside the switch
def isPieceNOTInSwitch(conveyorSensor,switcher,read):
    if(readSensor(read, conveyorSensor) and not readSensor(read, switcher.regIsPieceIn)):
        return True
    else:
        return False
    
## check if a piece is inside the switch
##(when switcher sensor and conveyor sensor belongs to different registers)
def isPieceNOTInSwitchINEX(conveyorSensor,switcher,readIn,readEx):
    if(readSensor(readEx, conveyorSensor) and not readSensor(readIn, switcher.regIsPieceIn)):
        return True
    else:
        return False

## check if a switch has finished its movement
def isSwitcherFinished(switcher,read):
    if(readSensor(read, switcher.regIsPosReached) and not readSensor(read, switcher.regIsMoving)):
        return True
    else:
        return False

## check if next conveyor and switch are free
def checkNextCandS(nextC, nextS):
    if(nextC.isFree and nextS.isFree):
        nextC.isFree = False
        nextS.isFree = False
        return True
    else:
        return False
## check if next conveyor is free
def checkNextC(nextC):
    if(nextC.isFree):
        nextC.isFree = False
        return True
    else:
        return False

##set free conveyor and switch 
def setFreeCandS(prevC, prevS):
    prevC.isFree = True
    prevS.isFree = True

##set free conveyor 
def setFreeC(prevC):
    prevC.isFree = True
    
##Check if a piece should go to the storage or exit
def toExit(deliverytime):
    a = datetime.now()
    b = deliverytime - a
    c = b.total_seconds()
    print(c)
    if(c < 120):
        return True
    else:
        return False
    
#######################################################################
## PATH CLASSES
##
## A PIECE IS COMING FROM INPUT AND GOING THROUGH MACHINING 1 
class PathM1(object):
    def __init__(self):
        #variable for the current step
        self.step = 0
        self.processed = False
        self.stopConv = False
        
        #based on the current step execute an action
    def execute(self,inWrite,exWrite,inRead,exRead,thispiece):
        if(self.step == 0):
            #A piece from input received
            if(readSensor(inRead, conveyors["incU"].regIsEndSensor)):
                iclient.notifyPieceRecieved()
                inWrite=startConveyor(conveyors["incU"], 0, inWrite)
                inWrite=startSwitcher(switches["insO"], 2, inWrite)
                self.step=self.step+1
        elif(self.step == 1):
            if(isPieceInSwitch(conveyors["incU"].regIsBeginSensor, switches["insO"], inRead)):
                inWrite=stopConveyor(conveyors["incU"], inWrite)
                self.step=self.step+1
        elif(self.step == 2):
            if(checkNextCandS(conveyors["incV"], switches["insN"])):       
                inWrite=startSwitcher(switches["insO"], 3, inWrite)
                self.step=self.step+1
        elif(self.step == 3):
            if(isSwitcherFinished(switches["insO"], inRead)):
                inWrite=startConveyor(conveyors["incV"], 1, inWrite)
                inWrite=startConveyor(conveyors["incL"], 1, inWrite)
                inWrite=startSwitcher(switches["insN"], 3, inWrite)
                self.step=self.step+1
        elif(self.step == 4):
            if(isPieceNOTInSwitch(conveyors["incV"].regIsBeginSensor, switches["insO"], inRead)):
                setFreeCandS(conveyors["incU"], switches["insO"])
                self.step=self.step+1
        
        elif(self.step == 5):
            if(isPieceInSwitch(conveyors["incL"].regIsEndSensor, switches["insN"], inRead)):
                inWrite=stopConveyor(conveyors["incV"], inWrite)
                inWrite=stopConveyor(conveyors["incL"], inWrite)
                self.step=self.step+1
        elif(self.step == 6):
            if(checkNextC(conveyors["incM"])):  
                inWrite=startSwitcher(switches["insN"], 2, inWrite)
                self.step=self.step+1
        elif(self.step == 7):
            if(isSwitcherFinished(switches["insN"], inRead)):
                inWrite=startConveyor(conveyors["incM"], 0, inWrite)
                self.step=self.step+1
        elif(self.step == 8):
            if(isPieceNOTInSwitch(conveyors["incM"].regIsEndSensor, switches["insN"], inRead)):
                setFreeCandS(conveyors["incV"], switches["insN"])
                setFreeC(conveyors["incL"])
                self.step=self.step+1
                
        elif(self.step == 9):
            if(readSensor(inRead, conveyors["incM"].regIsBeginSensor)):
                inWrite=stopConveyor(conveyors["incM"], inWrite)
                self.step=self.step+1
                
        #MACHINING 1
        elif(self.step == 10):
            resp = mclient.passPieceM1(thispiece)
            if(resp == 1):
                inWrite=startConveyor(conveyors["incM"], 0, inWrite)
                self.step=self.step+1
        elif(self.step == 11):
            if(self.stopConv):
                self.stopConv = False
                inWrite=stopConveyor(conveyors["incM"], inWrite)
                setFreeC(conveyors["incM"])
                self.step=self.step+1
        elif(self.step == 12):
            if(self.processed):
                self.processed=False
                inWrite=startConveyor(conveyors["incR"], 0, inWrite)
                inWrite=startSwitcher(switches["insK"], 3, inWrite)
                self.step=self.step+1
                
        elif(self.step == 13):
            if(isPieceInSwitch(conveyors["incR"].regIsBeginSensor, switches["insK"], inRead)):
                inWrite=stopConveyor(conveyors["incR"], inWrite)
                self.step=self.step+1
        elif(self.step == 14):
            if(checkNextCandS(conveyors["excL"], switches["exsM"])):       
                inWrite=startSwitcher(switches["insK"], 2, inWrite)
                self.step=self.step+1
        elif(self.step == 15):
            if(isSwitcherFinished(switches["insK"], inRead)):
                exWrite=startConveyor(conveyors["excL"], 0, exWrite)
                exWrite=startSwitcher(switches["exsM"], 2, exWrite)
                self.step=self.step+1
        elif(self.step == 16):
            if(isPieceNOTInSwitchINEX(conveyors["excL"].regIsEndSensor, switches["insK"], inRead, exRead)):
                setFreeCandS(conveyors["incR"], switches["insK"])
                self.step=self.step+1
        
        elif(self.step == 17):
            if(isPieceInSwitch(conveyors["excL"].regIsBeginSensor, switches["exsM"], exRead)):
                exWrite=stopConveyor(conveyors["excL"], exWrite)
                self.step=self.step+1
        elif(self.step == 18):
            if(checkNextCandS(conveyors["excN"], switches["exsR"])):       
                exWrite=startSwitcher(switches["exsM"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 19):
            if(isSwitcherFinished(switches["exsM"], exRead)):
                exWrite=startConveyor(conveyors["excN"], 1, exWrite)
                exWrite=startSwitcher(switches["exsR"], 3, exWrite)
                self.step=self.step+1
        elif(self.step == 20):
            if(isPieceNOTInSwitch(conveyors["excN"].regIsBeginSensor, switches["exsM"], exRead)):
                setFreeCandS(conveyors["excL"], switches["exsM"])
                self.step=self.step+1
                
        elif(self.step == 21):
            if(isPieceInSwitch(conveyors["excN"].regIsEndSensor, switches["exsR"], exRead)):
                exWrite=stopConveyor(conveyors["excN"], exWrite)
                self.step=self.step+1
        elif(self.step == 22):
            if(checkNextCandS(conveyors["excP"], switches["exsS"])):    
                exWrite=startSwitcher(switches["exsR"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 23):
            if(isSwitcherFinished(switches["exsR"], exRead)):
                exWrite=startConveyor(conveyors["excP"], 1, exWrite)
                exWrite=startSwitcher(switches["exsS"], 2, exWrite)
                self.step=self.step+1
        elif(self.step == 24):
            if(isPieceNOTInSwitch(conveyors["excP"].regIsBeginSensor, switches["exsR"], exRead)):
                setFreeCandS(conveyors["excN"], switches["exsR"])
                self.step=self.step+1
                
        elif(self.step == 25):
            if(isPieceInSwitch(conveyors["excP"].regIsEndSensor, switches["exsS"], exRead)):
                exWrite=stopConveyor(conveyors["excP"], exWrite)
                self.step=self.step+1
        elif(self.step == 26):
            if(checkNextCandS(conveyors["excT"], switches["exsQ"])):       
                exWrite=startSwitcher(switches["exsS"], 3, exWrite)
                self.step=self.step+1
        elif(self.step == 27):
            if(isSwitcherFinished(switches["exsS"], exRead)):
                exWrite=startConveyor(conveyors["excT"], 0, exWrite)
                exWrite=startSwitcher(switches["exsQ"], 2, exWrite)
                self.step=self.step+1
        elif(self.step == 28):
            if(isPieceNOTInSwitch(conveyors["excT"].regIsEndSensor, switches["exsS"], exRead)):
                setFreeCandS(conveyors["excP"], switches["exsS"])
                self.step=self.step+1
                
        elif(self.step == 29):
            if(isPieceInSwitch(conveyors["excT"].regIsBeginSensor, switches["exsQ"], exRead)):
                exWrite=stopConveyor(conveyors["excT"], exWrite)
                self.step=self.step+1
        elif(self.step == 30):
            if(toExit(thispiece.PlannedDeliveryTime)):
                if(checkNextC(conveyors["excV"])):       
                    exWrite=startSwitcher(switches["exsQ"], 1, exWrite)
                    self.step=self.step+1
            else:
                if(checkNextC(conveyors["excU"])):       
                    exWrite=startSwitcher(switches["exsQ"], 3, exWrite)
                    self.step=50
####################EXIT################## 
        elif(self.step == 31):
            if(isSwitcherFinished(switches["exsQ"], exRead)):
                exWrite=startConveyor(conveyors["excV"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 32):
            if(isPieceNOTInSwitch(conveyors["excV"].regIsBeginSensor, switches["exsQ"], exRead)):
                setFreeCandS(conveyors["excT"], switches["exsQ"])
                self.step=self.step+1
        elif(self.step == 33):
            if(readSensor(exRead, conveyors["excV"].regIsEndSensor)):
                exWrite=stopConveyor(conveyors["excV"], exWrite)
                self.step=self.step+1
                #EXIT METHOD
        elif(self.step == 34):
            if(eclient.askIsFree()):
                eclient.passPiece(thispiece)
                exWrite=startConveyor(conveyors["excV"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 35):
            #EXIT RECIEVED RECIEVED PIECE
            if(self.stopConv):
                self.stopConv = False
                exWrite=stopConveyor(conveyors["excV"], exWrite)
                setFreeC(conveyors["excV"])
                piecesToDelete.append(thispiece)
                self.step=self.step+1
###############STORAGE#######################################################
        elif(self.step == 50):
            if(isSwitcherFinished(switches["exsQ"], exRead)):
                exWrite=startConveyor(conveyors["excU"], 0, exWrite) 
                self.step=self.step+1
        elif(self.step == 51):
            if(isPieceNOTInSwitch(conveyors["excU"].regIsEndSensor, switches["exsQ"], exRead)):
                setFreeCandS(conveyors["excT"], switches["exsQ"])
                self.step=self.step+1
        
        elif(self.step == 52):
            if(readSensor(exRead, separators["V1"].regIsPieceInfront)):
                self.step=self.step+1
        elif(self.step == 53):
            if(not readSensor(exRead, separators["V1"].regIsPieceInfront)):
                exWrite=activateSeparator(separators["V1"], exWrite)  
                self.step=self.step+1
        elif(self.step == 54):
            if(readSensor(exRead, conveyors["excU"].regIsBeginSensor)):
                exWrite=activateSeparator(separators["V1"], exWrite)
                self.step=self.step+1
                
###################CALL STORAGE METHOD
        elif(self.step == 55):
            ans = sclient.askIsFree()
            print("ask")
            print(ans)
            if(ans == "True"):
                sclient.passPiece(thispiece)
                exWrite=stopConveyor(conveyors["excU"], exWrite)
                self.step=self.step+1
        elif(self.step == 56):
            if(self.stopConv):
                self.stopConv = False
                setFreeC(conveyors["excU"])
                piecesToDelete.append(thispiece) 
                self.step=self.step+1
        return inWrite, exWrite

## A PIECE IS COMING FROM INPUT AND GOING THROUGH MACHINING 2
class PathM2(object):
    def __init__(self):
        self.step = 0
        self.processed = False
        self.stopConv = False
        
    def execute(self,inWrite,exWrite,inRead,exRead,thispiece):
        if(self.step == 0):
            if(readSensor(inRead, conveyors["incU"].regIsEndSensor)):
                iclient.notifyPieceRecieved()
                inWrite=startConveyor(conveyors["incU"], 0, inWrite)
                inWrite=startSwitcher(switches["insO"], 2, inWrite)
                self.step=self.step+1
        elif(self.step == 1):
            if(isPieceInSwitch(conveyors["incU"].regIsBeginSensor, switches["insO"], inRead)):
                inWrite=stopConveyor(conveyors["incU"], inWrite)
                self.step=self.step+1
        elif(self.step == 2):
            if(checkNextCandS(conveyors["incP"], switches["insS"])):       
                inWrite=startSwitcher(switches["insO"], 1, inWrite)
                self.step=self.step+1
        elif(self.step == 3):
            if(isSwitcherFinished(switches["insO"], inRead)):
                inWrite=startConveyor(conveyors["incP"], 0, inWrite)
                inWrite=startSwitcher(switches["insS"], 1, inWrite)
                self.step=self.step+1
        elif(self.step == 4):
            if(isPieceNOTInSwitch(conveyors["incP"].regIsEndSensor, switches["insO"], inRead)):
                setFreeCandS(conveyors["incU"], switches["insO"])
                self.step=self.step+1
        
        elif(self.step == 5):
            if(isPieceInSwitch(conveyors["incP"].regIsBeginSensor, switches["insS"], inRead)):
                inWrite=stopConveyor(conveyors["incP"], inWrite)
                self.step=self.step+1
        elif(self.step == 6):
            if(checkNextCandS(conveyors["incR"], switches["insK"])):       
                inWrite=startSwitcher(switches["insS"], 2, inWrite)
                self.step=self.step+1
        elif(self.step == 7):
            if(isSwitcherFinished(switches["insS"], inRead)):
                inWrite=startConveyor(conveyors["incR"], 0, inWrite)
                inWrite=startSwitcher(switches["insK"], 3, inWrite)
                self.step=self.step+1
        elif(self.step == 8):
            if(isPieceNOTInSwitch(conveyors["incR"].regIsEndSensor, switches["insS"], inRead)):
                setFreeCandS(conveyors["incP"], switches["insS"])
                self.step=self.step+1
        
        elif(self.step == 9):
            if(isPieceInSwitch(conveyors["incR"].regIsBeginSensor, switches["insK"], inRead)):
                inWrite=stopConveyor(conveyors["incR"], inWrite)
                self.step=self.step+1
        elif(self.step == 10):
            if(checkNextCandS(conveyors["excL"], switches["exsM"])):       
                inWrite=startSwitcher(switches["insK"], 2, inWrite)
                self.step=self.step+1
        elif(self.step == 11):
            if(isSwitcherFinished(switches["insK"], inRead)):
                exWrite=startConveyor(conveyors["excL"], 0, exWrite)
                exWrite=startSwitcher(switches["exsM"], 2, exWrite)
                self.step=self.step+1
        elif(self.step == 12):
            if(isPieceNOTInSwitchINEX(conveyors["excL"].regIsEndSensor, switches["insK"], inRead, exRead)):
                setFreeCandS(conveyors["incR"], switches["insK"])
                self.step=self.step+1
        
        elif(self.step == 13):
            if(isPieceInSwitch(conveyors["excL"].regIsBeginSensor, switches["exsM"], exRead)):
                exWrite=stopConveyor(conveyors["excL"], exWrite)
                self.step=self.step+1
        elif(self.step == 14):
            if(checkNextCandS(conveyors["excN"], switches["exsR"])):       
                exWrite=startSwitcher(switches["exsM"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 15):
            if(isSwitcherFinished(switches["exsM"], exRead)):
                exWrite=startConveyor(conveyors["excN"], 1, exWrite)
                exWrite=startSwitcher(switches["exsR"], 3, exWrite)
                self.step=self.step+1
        elif(self.step == 16):
            if(isPieceNOTInSwitch(conveyors["excN"].regIsBeginSensor, switches["exsM"], exRead)):
                setFreeCandS(conveyors["excL"], switches["exsM"])
                self.step=self.step+1
                
        elif(self.step == 17):
            if(isPieceInSwitch(conveyors["excN"].regIsEndSensor, switches["exsR"], exRead)):
                exWrite=stopConveyor(conveyors["excN"], exWrite)
                self.step=self.step+1
        elif(self.step == 18):
            if(checkNextC(conveyors["excO"])):       
                exWrite=startSwitcher(switches["exsR"], 2, exWrite)
                self.step=self.step+1
        elif(self.step == 19):
            if(isSwitcherFinished(switches["exsR"], exRead)):
                exWrite=startConveyor(conveyors["excO"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 20):
            if(isPieceNOTInSwitch(conveyors["excO"].regIsBeginSensor, switches["exsR"], exRead)):
                setFreeCandS(conveyors["excN"], switches["exsR"])
                self.step=self.step+1
                
        elif(self.step == 21):
            if(readSensor(exRead, conveyors["excO"].regIsEndSensor)):
                exWrite=stopConveyor(conveyors["excO"], exWrite)
                self.step=self.step+1
###############M2 METHOD
        elif(self.step == 22):
            resp = mclient.passPieceM2(thispiece)
            if(resp == 1):
                exWrite=startConveyor(conveyors["excO"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 23):
          # M2 RECIEVED RECIEVED PIECE
            if(self.stopConv):
                self.stopConv = False
                exWrite=stopConveyor(conveyors["excO"], exWrite)
                setFreeC(conveyors["excO"])
                self.step=self.step+1
        elif(self.step == 24):
            if(self.processed):
                self.processed=False
                inWrite=startConveyor(conveyors["incR"], 1, inWrite)
                inWrite=startSwitcher(switches["insS"], 2, inWrite)
                self.step=self.step+1
###############################################################
        elif(self.step == 25):
            if(isPieceInSwitch(conveyors["incR"].regIsEndSensor, switches["insS"], inRead)):
                setFreeC(conveyors["excO"])
                inWrite=stopConveyor(conveyors["incR"], inWrite)
                self.step=self.step+1
        elif(self.step == 26):
            if(checkNextCandS(conveyors["excW"], switches["exsS"])):       
                inWrite=startSwitcher(switches["insS"], 3, inWrite)
                self.step=self.step+1
        elif(self.step == 27):
            if(isSwitcherFinished(switches["insS"], inRead)):
                exWrite=startConveyor(conveyors["excW"], 0, exWrite)
                exWrite=startSwitcher(switches["exsS"], 1, exWrite)
                self.step=self.step+1
        #ERRRRROR
        elif(self.step == 28):
            if(isPieceNOTInSwitchINEX(conveyors["excW"].regIsEndSensor, switches["insS"], inRead, exRead)):
                setFreeCandS(conveyors["incR"], switches["insS"])
                self.step=self.step+1
                
        elif(self.step == 29):
            if(isPieceInSwitch(conveyors["excW"].regIsBeginSensor, switches["exsS"], exRead)):
                exWrite=stopConveyor(conveyors["excW"], exWrite)
                self.step=self.step+1
        elif(self.step == 30):
            if(checkNextCandS(conveyors["excT"], switches["exsQ"])):       
                exWrite=startSwitcher(switches["exsS"], 3, exWrite)
                self.step=self.step+1
        elif(self.step == 31):
            if(isSwitcherFinished(switches["exsS"], exRead)):
                exWrite=startConveyor(conveyors["excT"], 0, exWrite)
                exWrite=startSwitcher(switches["exsQ"], 2, exWrite)
                self.step=self.step+1
        elif(self.step == 32):
            if(isPieceNOTInSwitch(conveyors["excT"].regIsEndSensor, switches["exsS"], exRead)):
                setFreeCandS(conveyors["excW"], switches["exsS"])
                self.step=self.step+1
                
        elif(self.step == 33):
            if(isPieceInSwitch(conveyors["excT"].regIsBeginSensor, switches["exsQ"], exRead)):
                exWrite=stopConveyor(conveyors["excT"], exWrite)
                self.step=self.step+1
                
################EXIT OR STORAGE
        elif(self.step == 34):
            if(toExit(thispiece.PlannedDeliveryTime)):
                if(checkNextC(conveyors["excV"])):       
                    exWrite=startSwitcher(switches["exsQ"], 1, exWrite)
                    self.step=self.step+1
            else:
                if(checkNextC(conveyors["excU"])):       
                    exWrite=startSwitcher(switches["exsQ"], 3, exWrite)
                    self.step=50
####################EXIT################## 
        elif(self.step == 35):
            if(isSwitcherFinished(switches["exsQ"], exRead)):
                exWrite=startConveyor(conveyors["excV"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 36):
            if(isPieceNOTInSwitch(conveyors["excV"].regIsBeginSensor, switches["exsQ"], exRead)):
                setFreeCandS(conveyors["excT"], switches["exsQ"])
                self.step=self.step+1
        elif(self.step == 37):
            if(readSensor(exRead, conveyors["excV"].regIsEndSensor)):
                exWrite=stopConveyor(conveyors["excV"], exWrite)
                self.step=self.step+1
                #EXIT METHOD
        elif(self.step == 38):
            if(eclient.askIsFree()):
                eclient.passPiece(thispiece)
                exWrite=startConveyor(conveyors["excV"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 39):
            #EXIT RECIEVED RECIEVED PIECE
            if(self.stopConv):
                self.stopConv = False
                exWrite=stopConveyor(conveyors["excV"], exWrite)
                setFreeC(conveyors["excV"])
                piecesToDelete.append(thispiece)
                self.step=self.step+1
###############STORAGE#######################################################
        elif(self.step == 50):
            if(isSwitcherFinished(switches["exsQ"], exRead)):
                exWrite=startConveyor(conveyors["excU"], 0, exWrite) 
                self.step=self.step+1
        elif(self.step == 51):
            if(isPieceNOTInSwitch(conveyors["excU"].regIsEndSensor, switches["exsQ"], exRead)):
                exWrite=startConveyor(conveyors["excU"], 0, exWrite)
                setFreeCandS(conveyors["excT"], switches["exsQ"])
                self.step=self.step+1
        
        elif(self.step == 52):
            if(readSensor(exRead, separators["V1"].regIsPieceInfront)):
                self.step=self.step+1
        elif(self.step == 53):
            if(not readSensor(exRead, separators["V1"].regIsPieceInfront)):
                exWrite=activateSeparator(separators["V1"], exWrite)  
                self.step=self.step+1
        elif(self.step == 54):
            if(readSensor(exRead, conveyors["excU"].regIsBeginSensor)):
                exWrite=activateSeparator(separators["V1"], exWrite)  
                self.step=self.step+1
                
###################CALL STORAGE METHOD
        elif(self.step == 55):
            ans = sclient.askIsFree()
            if(ans == "True"):
                sclient.passPiece(thispiece)
                exWrite=stopConveyor(conveyors["excU"], exWrite)
                self.step=self.step+1
        elif(self.step == 56):
            if(self.stopConv):
                self.stopConv = False
                setFreeC(conveyors["excU"])
                piecesToDelete.append(thispiece) 
                self.step=self.step+1
                
        return inWrite, exWrite

## A PIECE IS COMING FROM INPUT AND GOING THROUGH MACHINING 1 AND MACHINING 2
class PathM1M2(object):
    def __init__(self):
        self.step = 0
        self.processed = False
        self.stopConv = False
         
    def execute(self,inWrite,exWrite,inRead,exRead,thispiece):
        if(self.step == 0):
            if(readSensor(inRead, conveyors["incU"].regIsEndSensor)):
                iclient.notifyPieceRecieved()
                inWrite=startConveyor(conveyors["incU"], 0, inWrite)
                inWrite=startSwitcher(switches["insO"], 2, inWrite)
                self.step=self.step+1
        elif(self.step == 1):
            if(isPieceInSwitch(conveyors["incU"].regIsBeginSensor, switches["insO"], inRead)):
                inWrite=stopConveyor(conveyors["incU"], inWrite)
                self.step=self.step+1
        elif(self.step == 2):
            if(checkNextCandS(conveyors["incV"], switches["insN"])):       
                inWrite=startSwitcher(switches["insO"], 3, inWrite)
                self.step=self.step+1
        elif(self.step == 3):
            if(isSwitcherFinished(switches["insO"], inRead)):
                inWrite=startConveyor(conveyors["incV"], 1, inWrite)
                inWrite=startConveyor(conveyors["incL"], 1, inWrite)
                inWrite=startSwitcher(switches["insN"], 3, inWrite)
                self.step=self.step+1
        elif(self.step == 4):
            if(isPieceNOTInSwitch(conveyors["incV"].regIsBeginSensor, switches["insO"], inRead)):
                setFreeCandS(conveyors["incU"], switches["insO"])
                self.step=self.step+1
        
        elif(self.step == 5):
            if(isPieceInSwitch(conveyors["incL"].regIsEndSensor, switches["insN"], inRead)):
                inWrite=stopConveyor(conveyors["incV"], inWrite)
                inWrite=stopConveyor(conveyors["incL"], inWrite)
                self.step=self.step+1
        elif(self.step == 6):
            if(checkNextC(conveyors["incM"])):  
                inWrite=startSwitcher(switches["insN"], 2, inWrite)
                self.step=self.step+1
        elif(self.step == 7):
            if(isSwitcherFinished(switches["insN"], inRead)):
                inWrite=startConveyor(conveyors["incM"], 0, inWrite)
                self.step=self.step+1
        elif(self.step == 8):
            if(isPieceNOTInSwitch(conveyors["incM"].regIsEndSensor, switches["insN"], inRead)):
                setFreeCandS(conveyors["incV"], switches["insN"])
                setFreeC(conveyors["incL"])
                self.step=self.step+1
                
        elif(self.step == 9):
            if(readSensor(inRead, conveyors["incM"].regIsBeginSensor)):
                inWrite=stopConveyor(conveyors["incM"], inWrite)
                self.step=self.step+1
                
        #MACHINING 1
        elif(self.step == 10):
            resp = mclient.passPieceM1(thispiece)
            if(resp == 1):
                inWrite=startConveyor(conveyors["incM"], 0, inWrite)
                self.step=self.step+1
        elif(self.step == 11):
            if(self.stopConv):
                self.stopConv = False
                inWrite=stopConveyor(conveyors["incM"], inWrite)
                setFreeC(conveyors["incM"])
                self.step=self.step+1
        elif(self.step == 12):
            if(self.processed):
                self.processed=False
                inWrite=startConveyor(conveyors["incR"], 0, inWrite)
                inWrite=startSwitcher(switches["insK"], 3, inWrite)
                self.step=self.step+1
                
        elif(self.step == 13):
            if(isPieceInSwitch(conveyors["incR"].regIsBeginSensor, switches["insK"], inRead)):
                inWrite=stopConveyor(conveyors["incR"], inWrite)
                self.step=self.step+1
        elif(self.step == 14):
            if(checkNextCandS(conveyors["excL"], switches["exsM"])):       
                inWrite=startSwitcher(switches["insK"], 2, inWrite)
                self.step=self.step+1
        elif(self.step == 15):
            if(isSwitcherFinished(switches["insK"], inRead)):
                exWrite=startConveyor(conveyors["excL"], 0, exWrite)
                exWrite=startSwitcher(switches["exsM"], 2, exWrite)
                self.step=self.step+1
        elif(self.step == 16):
            if(isPieceNOTInSwitchINEX(conveyors["excL"].regIsEndSensor, switches["insK"], inRead, exRead)):
                setFreeCandS(conveyors["incR"], switches["insK"])
                self.step=self.step+1
        
        elif(self.step == 17):
            if(isPieceInSwitch(conveyors["excL"].regIsBeginSensor, switches["exsM"], exRead)):
                exWrite=stopConveyor(conveyors["excL"], exWrite)
                self.step=self.step+1
        elif(self.step == 18):
            if(checkNextCandS(conveyors["excN"], switches["exsR"])):       
                exWrite=startSwitcher(switches["exsM"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 19):
            if(isSwitcherFinished(switches["exsM"], exRead)):
                exWrite=startConveyor(conveyors["excN"], 1, exWrite)
                exWrite=startSwitcher(switches["exsR"], 3, exWrite)
                self.step=self.step+1
        elif(self.step == 20):
            if(isPieceNOTInSwitch(conveyors["excN"].regIsBeginSensor, switches["exsM"], exRead)):
                setFreeCandS(conveyors["excL"], switches["exsM"])
                self.step=self.step+1
                
        elif(self.step == 21):
            if(isPieceInSwitch(conveyors["excN"].regIsEndSensor, switches["exsR"], exRead)):
                exWrite=stopConveyor(conveyors["excN"], exWrite)
                self.step=self.step+1
        elif(self.step == 22):
            if(checkNextC(conveyors["excO"])):       
                exWrite=startSwitcher(switches["exsR"], 2, exWrite)
                self.step=self.step+1
        elif(self.step == 23):
            if(isSwitcherFinished(switches["exsR"], exRead)):
                exWrite=startConveyor(conveyors["excO"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 24):
            if(isPieceNOTInSwitch(conveyors["excO"].regIsBeginSensor, switches["exsR"], exRead)):
                setFreeCandS(conveyors["excN"], switches["exsR"])
                self.step=self.step+1
                
        elif(self.step == 25):
            if(readSensor(exRead, conveyors["excO"].regIsEndSensor)):
                exWrite=stopConveyor(conveyors["excO"], exWrite)
                self.step=self.step+1
###############M2 METHOD
        elif(self.step == 26):
            resp = mclient.passPieceM2(thispiece)
            if(resp == 1):
                exWrite=startConveyor(conveyors["excO"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 27):
          # M2 RECIEVED RECIEVED PIECE
            if(self.stopConv):
                self.stopConv = False
                exWrite=stopConveyor(conveyors["excO"], exWrite)
                setFreeC(conveyors["excO"])
                self.step=self.step+1
        elif(self.step == 28):
            if(self.processed):
                self.processed=False
                inWrite=startConveyor(conveyors["incR"], 1, inWrite)
                inWrite=startSwitcher(switches["insS"], 2, inWrite)
                self.step=self.step+1
###############################################################
        elif(self.step == 29):
            if(isPieceInSwitch(conveyors["incR"].regIsEndSensor, switches["insS"], inRead)):
                setFreeC(conveyors["excO"])
                inWrite=stopConveyor(conveyors["incR"], inWrite)
                self.step=self.step+1
        elif(self.step == 30):
            if(checkNextCandS(conveyors["excW"], switches["exsS"])):       
                inWrite=startSwitcher(switches["insS"], 3, inWrite)
                self.step=self.step+1
        elif(self.step == 31):
            if(isSwitcherFinished(switches["insS"], inRead)):
                exWrite=startConveyor(conveyors["excW"], 0, exWrite)
                exWrite=startSwitcher(switches["exsS"], 1, exWrite)
                self.step=self.step+1
        #ERRRRROR
        elif(self.step == 32):
            if(isPieceNOTInSwitchINEX(conveyors["excW"].regIsEndSensor, switches["insS"], inRead, exRead)):
                setFreeCandS(conveyors["incR"], switches["insS"])
                self.step=self.step+1
                
        elif(self.step == 33):
            if(isPieceInSwitch(conveyors["excW"].regIsBeginSensor, switches["exsS"], exRead)):
                exWrite=stopConveyor(conveyors["excW"], exWrite)
                self.step=self.step+1
        elif(self.step == 34):
            if(checkNextCandS(conveyors["excT"], switches["exsQ"])):       
                exWrite=startSwitcher(switches["exsS"], 3, exWrite)
                self.step=self.step+1
        elif(self.step == 35):
            if(isSwitcherFinished(switches["exsS"], exRead)):
                exWrite=startConveyor(conveyors["excT"], 0, exWrite)
                exWrite=startSwitcher(switches["exsQ"], 2, exWrite)
                self.step=self.step+1
        elif(self.step == 36):
            if(isPieceNOTInSwitch(conveyors["excT"].regIsEndSensor, switches["exsS"], exRead)):
                setFreeCandS(conveyors["excW"], switches["exsS"])
                self.step=self.step+1
                
        elif(self.step == 37):
            if(isPieceInSwitch(conveyors["excT"].regIsBeginSensor, switches["exsQ"], exRead)):
                exWrite=stopConveyor(conveyors["excT"], exWrite)
                self.step=self.step+1
                
################EXIT OR STORAGE
        elif(self.step == 38):
            if(toExit(thispiece.PlannedDeliveryTime)):
                if(checkNextC(conveyors["excV"])):       
                    exWrite=startSwitcher(switches["exsQ"], 1, exWrite)
                    self.step=self.step+1
            else:
                if(checkNextC(conveyors["excU"])):       
                    exWrite=startSwitcher(switches["exsQ"], 3, exWrite)
                    self.step=50
####################EXIT################## 
        elif(self.step == 39):
            if(isSwitcherFinished(switches["exsQ"], exRead)):
                exWrite=startConveyor(conveyors["excV"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 40):
            if(isPieceNOTInSwitch(conveyors["excV"].regIsBeginSensor, switches["exsQ"], exRead)):
                setFreeCandS(conveyors["excT"], switches["exsQ"])
                self.step=self.step+1
        elif(self.step == 41):
            if(readSensor(exRead, conveyors["excV"].regIsEndSensor)):
                exWrite=stopConveyor(conveyors["excV"], exWrite)
                self.step=self.step+1
                #EXIT METHOD
        elif(self.step == 42):
            if(eclient.askIsFree()):
                eclient.passPiece(thispiece)
                exWrite=startConveyor(conveyors["excV"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 43):
            #EXIT RECIEVED RECIEVED PIECE
            if(self.stopConv):
                self.stopConv = False
                exWrite=stopConveyor(conveyors["excV"], exWrite)
                setFreeC(conveyors["excV"])
                piecesToDelete.append(thispiece)
                self.step=self.step+1
###############STORAGE#######################################################
        elif(self.step == 50):
            if(isSwitcherFinished(switches["exsQ"], exRead)):
                exWrite=startConveyor(conveyors["excU"], 0, exWrite) 
                self.step=self.step+1
        elif(self.step == 51):
            if(isPieceNOTInSwitch(conveyors["excU"].regIsEndSensor, switches["exsQ"], exRead)):
                setFreeCandS(conveyors["excT"], switches["exsQ"])
                self.step=self.step+1
        
        elif(self.step == 52):
            if(readSensor(exRead, separators["V1"].regIsPieceInfront)):
                self.step=self.step+1
        elif(self.step == 53):
            if(not readSensor(exRead, separators["V1"].regIsPieceInfront)):
                exWrite=activateSeparator(separators["V1"], exWrite)  
                self.step=self.step+1
        elif(self.step == 54):
            if(readSensor(exRead, conveyors["excU"].regIsBeginSensor)):
                exWrite=activateSeparator(separators["V1"], exWrite)
                self.step=self.step+1
                
###################CALL STORAGE METHOD
        elif(self.step == 55):
            ans = sclient.askIsFree()
            if(ans == "True"):
                sclient.passPiece(thispiece)
                exWrite=stopConveyor(conveyors["excU"], exWrite)
                self.step=self.step+1
        elif(self.step == 56):
            if(self.stopConv):
                self.stopConv = False
                setFreeC(conveyors["excU"])
                piecesToDelete.append(thispiece) 
                self.step=self.step+1
                
       
                
        return inWrite, exWrite

## A PIECE IS COMING FROM EXIT AND GOING TO STORAGE
class PathStorageToExit(object):
    def __init__(self):
        self.step = 0
        self.processed = False
        self.stopConv = False
         
    def execute(self,inWrite,exWrite,inRead,exRead,thispiece):
        if(self.step == 0):
            if(readSensor(exRead, conveyors["excU"].regIsBeginSensor)):
                exWrite=startConveyor(conveyors["excU"], 1, exWrite)
                exWrite=startSwitcher(switches["exsQ"], 3, exWrite) 
                exWrite=activateSeparator(separators["V1"], exWrite)  
                self.step=self.step+1
        elif(self.step == 1):
            if(readSensor(exRead, separators["V1"].regIsPieceBehind)):
                exWrite=activateSeparator(separators["V1"], exWrite)  
                self.step=self.step+1  
        elif(self.step == 2):
            if(isPieceInSwitch(conveyors["excU"].regIsEndSensor, switches["exsQ"], exRead)):
                exWrite=stopConveyor(conveyors["excU"], exWrite)
                self.step=self.step+1
        elif(self.step == 3):
            if(checkNextC(conveyors["excV"])):       
                exWrite=startSwitcher(switches["exsQ"], 1, exWrite)
                self.step=self.step+1
        
        elif(self.step == 4):
            if(isSwitcherFinished(switches["exsQ"], exRead)):
                exWrite=startConveyor(conveyors["excV"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 5):
            if(isPieceNOTInSwitch(conveyors["excV"].regIsBeginSensor, switches["exsQ"], exRead)):
                setFreeCandS(conveyors["excU"], switches["exsQ"])
                self.step=self.step+1
        elif(self.step == 6):
            if(readSensor(exRead, conveyors["excV"].regIsEndSensor)):
                exWrite=stopConveyor(conveyors["excV"], exWrite)
                self.step=self.step+1
                #EXIT METHOD
        elif(self.step == 7):
            if(eclient.askIsFree()):
                eclient.passPiece(thispiece)
                exWrite=startConveyor(conveyors["excV"], 1, exWrite)
                self.step=self.step+1
        elif(self.step == 8):
            #EXIT RECIEVED RECIEVED PIECE
            if(self.stopConv):
                self.stopConv = False
                exWrite=stopConveyor(conveyors["excV"], exWrite)
                setFreeC(conveyors["excV"])
                piecesToDelete.append(thispiece)
                self.step=self.step+1
        return inWrite, exWrite
        

#######################################
## HARDWARE CLASSES
class Switch(object):
    def __init__(self,name,group,regHome,regPos1,regPos2,regPos3,
                 regIsPosReached,regIsMoving,regIsPieceIn):
        self.name = name
        self.group = group
        self.regHome = regHome
        self.regPos1 = regPos1
        self.regPos2 = regPos2
        self.regPos3 = regPos3
        self.regIsPosReached = regIsPosReached
        self.regIsMoving = regIsMoving
        self.regIsPieceIn = regIsPieceIn
        self.isFree = True
        self.currentPos = "calibrated"
        
class Conveyor(object):
    def __init__(self,name,group,regForward,regBack,regSpeed,regIsBeginSensor,regIsEndSensor):
        self.name = name
        self.group = group
        self.regForward = regForward
        self.regBack = regBack
        self.regSpeed = regSpeed
        self.regIsBeginSensor = regIsBeginSensor
        self.regIsEndSensor = regIsEndSensor
        self.isFree = True
        self.beginSensor = 0
        self.endSensor = 0
        
class Separator(object):
    def __init__(self,name,regActuator,regIsPieceBehind,regIsPieceInfront):
        self.name = name
        self.regActuator = regActuator
        self.regIsPieceBehind = regIsPieceBehind
        self.regIsPieceInfront = regIsPieceInfront
        self.isFree = True
        self.behindSensor = 0
        self.infrontSensor = 0

## 3 functions for initialization with given bits
def initSwitches():
    d = {}
    d["insO"] = Switch('O',"input",38,39,40,41,38,39,40)
    d["insN"] = Switch('N',"input",34,35,36,37,34,35,36)
    d["insK"] = Switch('K',"input",26,27,28,29,26,27,28)
    d["insS"] = Switch('S',"input",48,49,50,51,48,49,50)
    d["exsM"] = Switch('M',"exit",30,31,32,33,30,31,32)
    d["exsR"] = Switch('R',"exit",44,45,46,47,44,45,46)
    d["exsS"] = Switch('S',"exit",48,49,50,51,48,49,50)
    d["exsQ"] = Switch('Q',"exit",40,41,42,43,40,41,42)
    return d
    
def initConveyors():
    d = {}
    d["incU"] = Conveyor('U',"input",56,57,13,56,57)
    d["incV"] = Conveyor('V',"input",58,59,13,58,59)
    d["incL"] = Conveyor('L',"input",30,31,7,30,31)
    d["incM"] = Conveyor('M',"input",32,33,8,32,33)
    d["incR"] = Conveyor('R',"input",46,47,11,46,47)
    d["incP"] = Conveyor('P',"input",42,43,9,42,43)
    d["excL"] = Conveyor('L',"exit",28,29,8,28,29)
    d["excN"] = Conveyor('N',"exit",34,35,9,34,35)
    d["excO"] = Conveyor('O',"exit",36,37,10,36,37)
    d["excP"] = Conveyor('P',"exit",38,39,11,38,39)
    d["excW"] = Conveyor('W',"exit",58,59,15,58,59)
    d["excT"] = Conveyor('T',"exit",52,53,12,52,53)
    d["excU"] = Conveyor('U',"exit",54,55,13,54,55)
    d["excV"] = Conveyor('V',"exit",56,57,14,56,57)
    return d

def initSeparators():
    d = {}
    d["V1"] = Separator(1,64,65,66)
    return d

####################################
##SERVER AND CLIENTS CLASSES

class OPCUA_Server(OurProduct):
    def __init__(self, endpoint, name):
        #Configuration
        print("Init", name, "...")
        self.name = name
        self.server = Server ()
        self.my_namespace_name = 'http://hs-emden-leer.de/OurProduct/'
        self.my_namespace_idx = self.server.register_namespace(self.my_namespace_name)
        self.server.set_endpoint(endpoint)
        self.server.set_server_name(name)
       
        #Add new object - MyModule
        self.objects = self.server.get_objects_node()
        self.finput = self.objects.add_object(self.my_namespace_idx, "ForInput")
        self.fmashining = self.objects.add_object(self.my_namespace_idx, "ForMachining")
        self.fstorage = self.objects.add_object(self.my_namespace_idx, "ForStorage")
        self.fexit = self.objects.add_object(self.my_namespace_idx, "ForExit")
        self.fhmi = self.objects.add_object(self.my_namespace_idx, "ForHMI")
        
        self.tstatus = self.fhmi.add_variable(self.my_namespace_idx,"tstatus",False)
        self.tred = self.fhmi.add_variable(self.my_namespace_idx,"tred",0)
        self.tblack = self.fhmi.add_variable(self.my_namespace_idx,"tblack",0)
        self.tsilver = self.fhmi.add_variable(self.my_namespace_idx,"tsilver",0)
        self.tstatus.set_writable()
        self.tred.set_writable()
        self.tblack.set_writable()
        self.tsilver.set_writable()
        #
        #pass in argument(s)
        self.create_our_product_type()
        inarg_ourproduct = ua.Argument()
        inarg_ourproduct.Name = "OurProduct"
        inarg_ourproduct.DataType = self.ourproduct_data.data_type
        inarg_ourproduct.ValueRank = -1 
        inarg_ourproduct.ArrayDimensions = []
        inarg_ourproduct.Description = ua.LocalizedText("A new Product")

        #pass out argument 
        outarg_answer = ua.Argument()
        outarg_answer.Name = "Answer"
        outarg_answer.DataType = ua.NodeId(ua.ObjectIds.String)
        outarg_answer.ValueRank = -1 
        outarg_answer.ArrayDimensions = []
        outarg_answer.Description = ua.LocalizedText("Here you can specify an answer")
        
        #pass methods
        self.finput.add_method(self.my_namespace_idx, "inputPass", inputPass, [inarg_ourproduct], [outarg_answer])
        self.fmashining.add_method(self.my_namespace_idx, "m1Pass", m1Pass, [inarg_ourproduct], [outarg_answer])
        self.fmashining.add_method(self.my_namespace_idx, "m2Pass", m2Pass, [inarg_ourproduct], [outarg_answer])
        self.fstorage.add_method(self.my_namespace_idx, "storagePass", storagePass, [inarg_ourproduct], [outarg_answer])
        
        #recieve methods
        self.fmashining.add_method(self.my_namespace_idx, "m1Received", m1Received, [inarg_ourproduct], [outarg_answer])
        self.fmashining.add_method(self.my_namespace_idx, "m2Received", m2Received, [inarg_ourproduct], [outarg_answer])
        self.fstorage.add_method(self.my_namespace_idx, "storageReceived", storageReceived, [inarg_ourproduct], [outarg_answer])
        self.fexit.add_method(self.my_namespace_idx, "exitReceived", exitReceived, [inarg_ourproduct], [outarg_answer])
        
        #check answer
        outarg = ua.Argument()
        outarg.Name = "Answer"
        outarg.DataType = ua.NodeId(ua.ObjectIds.Boolean)
        outarg.ValueRank = -1 
        outarg.ArrayDimensions = []
        outarg.Description = ua.LocalizedText("Here you can specify an answer")
        
        #check methods
        self.finput.add_method(self.my_namespace_idx, "inputCheck", inputCheck, [], [outarg])
        self.fmashining.add_method(self.my_namespace_idx, "m1Check", m1Check, [], [outarg])
        self.fmashining.add_method(self.my_namespace_idx, "m2Check", m2Check, [], [outarg])
        self.fstorage.add_method(self.my_namespace_idx, "storageCheck", storageCheck, [], [outarg])
        

    def __enter__(self) :
        #Start server
        print("Setup", self.name, "....")
        self.server.start()
        return self
    
    def __exit__(self, exc, exc_val, exc_tb) :
        #Close server
        print("Closing", self.name, "....")
        self.server.stop()
        
## Two functions to change the server variables and set the current number 
## of pieces in the flow (used by HMI)

def plusPiece(p):
    if(p.PartClassID == uuid.UUID("d0a135f2-ac3a-485e-baff-b17f8ca32039")):
        tserv.tred.set_value(tserv.tred.get_value()+1)
    elif(p.PartClassID == uuid.UUID("e3d3e558-a086-48f3-8774-c103fe23fe6d")):
        tserv.tblack.set_value(tserv.tblack.get_value()+1)
    elif(p.PartClassID == uuid.UUID("1c2045df-a8aa-4899-bd7d-ed6dcedbc4ee")):
        tserv.tsilver.set_value(tserv.tsilver.get_value()+1)

def minusPiece(p):
    if(p.PartClassID == uuid.UUID("d0a135f2-ac3a-485e-baff-b17f8ca32039")):
        tserv.tred.set_value(tserv.tred.get_value()-1)
    elif(p.PartClassID == uuid.UUID("e3d3e558-a086-48f3-8774-c103fe23fe6d")):
        tserv.tblack.set_value(tserv.tblack.get_value()-1)
    elif(p.PartClassID == uuid.UUID("1c2045df-a8aa-4899-bd7d-ed6dcedbc4ee")):
        tserv.tsilver.set_value(tserv.tsilver.get_value()-1)

##input
class Input_Client():
    ##say that piece is recieved
    def notifyPieceRecieved(self):
        client = Client("opc.tcp://192.168.200.167:48844")
        client.connect()
        mynamespace_idx = client.get_namespace_index("http://hs-emden-leer.de/OurProduct/")
        root = client.get_root_node()
        iobj = root.get_child(["0:Objects", "{}:Input_Transport".format(mynamespace_idx)])
        client.load_type_definitions() 
        
        res = iobj.call_method("{}:conv_check".format(mynamespace_idx))
        
        client.disconnect()

##machining
class Machining_Client():
    ##pass a piece object
    def passPieceM1(self,piece):
        client = Client("opc.tcp://192.168.200.200:4840/Machining")
        client.connect()
        
        mynamespace_idx = client.get_namespace_index("http://hs-emden-leer.de/OurProduct/")
        root = client.get_objects_node()
        
        machvar = client.get_namespace_index("Machining Server")
        
        m1obj = root.get_child(["%d:Machine_1" % machvar])
        m2obj = root.get_child(["%d:Machine_2" % machvar])
        
        client.load_type_definitions() 
        res = m1obj.call_method("{}:input".format(machvar),piece)
        client.disconnect()
        return res
        
    def passPieceM2(self,piece):
        client = Client("opc.tcp://192.168.200.200:4840/Machining")
        client.connect()
        
        mynamespace_idx = client.get_namespace_index("http://hs-emden-leer.de/OurProduct/")
        root = client.get_objects_node()
        
        machvar = client.get_namespace_index("Machining Server")
        
        m1obj = root.get_child(["%d:Machine_1" % machvar])
        m2obj = root.get_child(["%d:Machine_2" % machvar])
        
        client.load_type_definitions() 
        
        res = m2obj.call_method("{}:input".format(machvar),piece)
        client.disconnect()
        return res
    
class Storage_Client():
    ##ask is free
    def askIsFree(self):
        client = Client("opc.tcp://192.168.200.168:51993")
        client.connect()
        
        mynamespace_idx = client.get_namespace_index("http://hs-emden-leer.de/OurProduct/")
        root = client.get_root_node()
        sobj = root.get_child(["0:Objects", "{}:warehouse".format(mynamespace_idx)])
        client.load_type_definitions() 

        res = sobj.call_method("{}:storageCheck".format(mynamespace_idx))
        print("Receive answer is: ", res)
        
        client.disconnect()
        return res
     
    ##pass a piece object
    def passPiece(self,piece):
        client = Client("opc.tcp://192.168.200.168:51993")
        client.connect()
        
        mynamespace_idx = client.get_namespace_index("http://hs-emden-leer.de/OurProduct/")
        root = client.get_root_node()
        sobj = root.get_child(["0:Objects", "{}:warehouse".format(mynamespace_idx)])
        client.load_type_definitions() 

        res = sobj.call_method("{}:Received_store".format(mynamespace_idx),piece)
        print("Receive answer is: ", res)
        client.disconnect()
    
class Exit_Client():
    ##ask is free
    def askIsFree(self):
        client = Client("opc.tcp://192.168.200.152:40840")
        client.connect()
        
        mynamespace_idx = client.get_namespace_index("http://hs-emden-leer.de/OurProduct/")
        root = client.get_root_node()
        
        eobj = root.get_child(["0:Objects", "2:Transport", "{}:InputExit".format(mynamespace_idx)])
        
        
        client.load_type_definitions()
        
        res = eobj.call_method("{}:Is Input Ready".format(mynamespace_idx))
        
        client.disconnect()
        return res
    ##pass a piece object
    def passPiece(self,piece):
        client = Client("opc.tcp://192.168.200.152:40840")
        client.connect()
        
        mynamespace_idx = client.get_namespace_index("http://hs-emden-leer.de/OurProduct/")
        root = client.get_root_node()
        
        eobj = root.get_child(["0:Objects", "2:Transport", "{}:InputExit".format(mynamespace_idx)])
        
        
        client.load_type_definitions()
        
        res = eobj.call_method("{}:Incoming Piece".format(mynamespace_idx),piece)
        
        client.disconnect()
        
## HMI CLIENT AND SUBSCRIPTION HANDLER
## Commented out since it was impossible to have an always running client
## with multiple running client the program was not able to receive and send
## a full OurProduct object 
 
# class SubHandler(object):
#    
#     def event_notification(self, event):
#         print("New event recieved: ", event)
#         hclient.alert = True
#            
# class HMI_Client():
#         
#         
#     def __init__(self, endpoint):
#         self.client = Client(endpoint)
#           
#         
#     def __enter__(self):
#     
#         while(True):
#             try:
#                 self.client.connect()
#                 print("HMI client connected")
#                 break
#             except:
#                 print("Failed to connect HMI client")
#                 time.sleep(0.5)
#         self.alert = False
#         root = self.client.get_root_node()
#         
#             
#         obj = root.get_child(["0:Objects", "2:AllStop"])
#             
#         msclt = SubHandler()
#         sub = self.client.create_subscription(100, msclt)
#             
#             
#         handle = sub.subscribe_events(obj)
#             
#     
#         return self
#         
#     def __exit__(self, exc_type, exc_val, exc_tb):
#         print("Disconnecting....")
#         self.client.disconnect()



#######################################
## FUNCTIONS FOR WORKING WITH REGISTERS

## read input registers
def readInput():
    inputRead = inputClient.read_holding_registers(0, 6)
    if inputRead:
        return inputRead
    else:
        return "error"
    
## write input registerss
def writeInput(inRegs):
    inputClient.write_multiple_registers(8018, [inRegs[1],inRegs[0],inRegs[3],inRegs[2]])
    
## read exit registers
def readExit():
    exitRead = exitClient.read_holding_registers(0, 8)
    if exitRead:
        return exitRead
    else:
        return("error")
    
## write exit registers
def writeExit(exRegs):
    exitClient.write_multiple_registers(8018, [exRegs[1],exRegs[0],exRegs[3],exRegs[2],exRegs[5],exRegs[4]])

## remove a bit from registers
def clearBits(var,registers):
    new_regs =list(registers)
    value = var % 16
    new_regs[var // 16] = new_regs[var // 16] - (1 << value)
    return new_regs  

## write a bit to registers
def writeBits(var, registers):
    if(var < 0 or var > 95):
        print("wrong value")
        return
    new_regs = list(registers)
    value = var % 16
    new_regs[var // 16] = new_regs[var // 16] | (1<<value)
    return new_regs

## read a bit from registers
def readSensor(regs,sensor):
    reg = 0
    if(sensor >=0 and sensor<=15):
        reg = 2
    elif(sensor>=16 and sensor<=31):
        reg = 1
    elif(sensor>=32 and sensor<=47):
        reg = 4
    elif(sensor>=48 and sensor<=63):
        reg = 3
    elif(sensor>=64 and sensor<=79):
        reg = 6    
    sensor = sensor%16
    if(regs[reg]&(1<<sensor) == (1<<sensor)):
        return True
    else:
        return False

## search for a bit in registers
def searchWriteBits(write, bit):
    new_regs = list(write)
    value = bit % 16
    if(new_regs[bit // 16]&(1<<value) == (1<<value)):
        return True
    else:
        return False
################################################################
## CALIBRATION

def switchCalibration(switches):
    inRegs = [0,0,0,0,0,0]
    exRegs = [0,0,0,0,0,0]

    print("prepare to switches calibration:") 
    for key in switches:
        if(switches[key].group == "input"):
            inRegs = writeBits(switches[key].regHome, inRegs)
        else:
            exRegs = writeBits(switches[key].regHome, exRegs)   
    print("input write:",inRegs) 
    print("exit write:",exRegs)
        
    if inputClient.write_multiple_registers(8018, [inRegs[1],inRegs[0],inRegs[3],inRegs[2]]):
        print("input switchers calibration done")
    else:
        print("input switchers calibration error")
        print("calibration cancelled")
        return
    
    if exitClient.write_multiple_registers(8018, [exRegs[1],exRegs[0],exRegs[3],exRegs[2]]):
        print("exit switchers calibration done")
    else:
        print("exit switchers calibration error")
        print("calibration cancelled")
        return
    
    while(True):
        calibrated = True
        ri = readInput()
        re = readExit()
        for key in switches:
            if(switches[key].group == "input"):
                if(readSensor(ri, switches[key].regIsMoving) or not readSensor(ri, switches[key].regIsPosReached)):
                    calibrated = False
            else:
                if(readSensor(re, switches[key].regIsMoving) or not readSensor(re, switches[key].regIsPosReached)):
                    calibrated = False      
        if(calibrated):
            break;
        time.sleep(0.5)
        
    print("switchers calibration finished")
    
####################################################################################
## PROGRAM START
if __name__ == '__main__' :

## Modbus clients creation
    inputClient = ModbusClient(host="192.168.200.235", port=502, auto_open=True)
    exitClient = ModbusClient(host="192.168.200.236", port=502, auto_open=True)
    
## Create a dictionary of objects for switches, conveyors and separators
    switches = initSwitches()
    conveyors = initConveyors()
    separators = initSeparators()

## Calibrate switches
    switchCalibration(switches)
    
##  Declare variables for 
    inRegs = [0,0,0,0,0,0]
    exRegs = [0,0,0,0,0,0]

##  Server object creation and setting the status variable 
    server_name = "TransportServer"
    endpoint_address = "opc.tcp://192.168.200.160:40840"
    tserv = OPCUA_Server(endpoint_address, server_name)
    tserv.tstatus.set_value(True)
    
##  Clients objects creation
    iclient = Input_Client()
    sclient = Storage_Client()
    mclient = Machining_Client()
    eclient = Exit_Client()
#     hclient = HMI_Client("opc.tcp://192.168.200.196:4240")

## MAIN WORKING LOOP
    with tserv:   
        try:
            while True:

                ## Read two registers
                ri = readInput()
                re = readExit()
                
                ## For every piece in the system execute its next step   
                for key in piecesAtFlow:
                    if(ri != "error" and re != "error"):
                        ## this function adds to both registers bits for existing pieces and
                        ## returns two complete registers to perform actions for all pieces at
                        ## the same time
                        out = piecesAtFlow[key].execute(inRegs, exRegs, ri, re,key)
                        
                        inRegs = out[0]
                        exRegs = out[1]
                    else:
                        print("cannot connect to the system")
                        
                ## Write two registers           
                writeInput(inRegs)
                writeExit(exRegs)
            
                ## Check if a new piece has arrived from the Input and add it to
                ## the pieces array
                if(len(piecesToAddInput) > 0):
                    for p in piecesToAddInput:
                        if(p.PartClassID == uuid.UUID("d0a135f2-ac3a-485e-baff-b17f8ca32039")):
                            piecesAtFlow[p] = PathM1M2()
                        elif(p.PartClassID == uuid.UUID("e3d3e558-a086-48f3-8774-c103fe23fe6d")):
                            piecesAtFlow[p] = PathM1()
                        elif(p.PartClassID == uuid.UUID("1c2045df-a8aa-4899-bd7d-ed6dcedbc4ee")):
                            piecesAtFlow[p] = PathM2()
                    piecesToAddInput.clear()
                
                ## Check if a new piece has arrived from the Storage and add it to
                ## the pieces array
                
                if(len(piecesToAddStorage) > 0):
                    for p in piecesToAddStorage:
                        plusPiece(p)
                        piecesAtFlow[p] = PathStorageToExit()
                    piecesToAddStorage.clear()   
                
                ## Check if a piece was processed and should be deleted from the
                ## pieces array
                
                if(len(piecesToDelete) > 0):
                    for p in piecesToDelete:
                        minusPiece(p)
                        piecesAtFlow.pop(p)
                    piecesToDelete.clear()
                    
##       HMI STOP EVENT SUBSCRIPTION
##       this code sends empty registers to stop the system after 
##       receiving the "stop" event notification from HMI, then it is waiting for
##       the "resume" event and continues working from the saved position

#                 if(hclient.alert):
#                     tserv.tstatus.set_value(False)   
#                     hclient.alert = False
#                     i = [0,0,0,0,0,0]
#                     e = [0,0,0,0,0,0]
#                     writeInput(i)
#                     writeExit(e)
#                     while(not hclient.alert):
#                         time.sleep(0.1)
#                     hclient.alert = False
#                     writeInput(inRegs)
#                     writeExit(exRegs)
                    
 
        except KeyboardInterrupt:
            print("Goodbye")