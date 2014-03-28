import sys
sys.path.append( '../ui' )

from PyQt4.QtGui import QApplication, QDialog
from ui_nodemanager import Ui_NodesManagerWidget
from uicustomizer import ManagerUiCustomizer

class NodesManager:

    def __init__( self ):
        
        self.app = QApplication( sys.argv )
        self.window = QDialog()
        self.ui = Ui_NodesManagerWidget()

        self.ui.setupUi( self.window )

        self.ucs = ManagerUiCustomizer( self.ui )
        self.ucs.addProgressBar( 0, 2 )
        self.ucs.addProgressBar( 0, 3 )
        self.ucs.appendRow( "UID1", "timestamp1" )

    def execute( self ):
        self.window.show()
        sys.exit(self.app.exec_())

if __name__ == "__main__":

    manager = NodesManager()
    manager.execute()
